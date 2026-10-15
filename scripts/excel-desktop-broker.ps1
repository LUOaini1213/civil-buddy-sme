# Fixed local desktop engine. The Rust host admits the package before this starts.
# Only host-created staging paths and bounded formula locations are accepted.
$ErrorActionPreference = 'Stop'
[Console]::InputEncoding = New-Object System.Text.UTF8Encoding($false)
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
function Emit-EngineMessage($value) { [Console]::WriteLine(($value | ConvertTo-Json -Depth 12 -Compress)); [Console]::Out.Flush() }
function Release-EngineCom($value) { if ($null -ne $value -and [Runtime.InteropServices.Marshal]::IsComObject($value)) { [void][Runtime.InteropServices.Marshal]::FinalReleaseComObject($value) } }
function Engine-Hash([string]$path) { (Get-FileHash -LiteralPath $path -Algorithm SHA256).Hash.ToLowerInvariant() }
function Resolve-EngineDesktopPath([string]$path) {
    # Rust canonicalization uses the extended local-drive prefix. Excel's COM
    # Open/SaveCopyAs requires the equivalent ordinary Win32 path.
    if ($path.StartsWith('\\?\')) {
        if ($path.Substring(4) -notmatch '^[A-Za-z]:[\\/]') { throw 'InvalidEnginePath' }
        $path = $path.Substring(4)
    }
    if ($path -notmatch '^[A-Za-z]:[\\/]') { throw 'InvalidEnginePath' }
    return [IO.Path]::GetFullPath($path)
}
function Assert-RegularEnginePath([string]$path) {
    if (-not [IO.Path]::IsPathRooted($path)) { throw 'InvalidEnginePath' }
    $probePath = [IO.Path]::GetFullPath($path)
    while ($null -ne $probePath -and $probePath.Length -gt 0) {
        if (Test-Path -LiteralPath $probePath) {
            $entry = Get-Item -LiteralPath $probePath -Force
            if (($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) { throw 'LinkedEnginePath' }
        }
        $parent = [IO.Path]::GetDirectoryName($probePath)
        if ($parent -eq $probePath) { break }; $probePath = $parent
    }
}
function New-EngineCalculationInput([string]$source) {
    # Consume only calculation requests in a separate staging copy. Some Excel
    # builds remain xlPending when these flags were true at Open, even after a
    # full rebuild. The original admitted bytes remain immutable and audited.
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = $null; $stream = $null; $reader = $null
    try {
        $archive = [IO.Compression.ZipFile]::OpenRead($source)
        $entry = $archive.GetEntry('xl/workbook.xml')
        if ($null -eq $entry -or $entry.Length -gt 4194304) { throw 'EngineCalculationInputConflict' }
        $settings = New-Object System.Xml.XmlReaderSettings
        $settings.DtdProcessing = [Xml.DtdProcessing]::Prohibit; $settings.XmlResolver = $null
        $stream = $entry.Open(); $reader = [Xml.XmlReader]::Create($stream, $settings)
        $document = New-Object System.Xml.XmlDocument
        $document.PreserveWhitespace = $true; $document.XmlResolver = $null; $document.Load($reader)
        $manager = New-Object System.Xml.XmlNamespaceManager($document.NameTable)
        $manager.AddNamespace('s','http://schemas.openxmlformats.org/spreadsheetml/2006/main')
        $nodes = $document.SelectNodes('/s:workbook/s:calcPr', $manager)
        if ($nodes.Count -gt 1) { throw 'EngineCalculationInputConflict' }
        $changes = @{}
        if ($nodes.Count -eq 1) {
            foreach ($name in @('forceFullCalc','fullCalcOnLoad')) {
                $old = $nodes[0].GetAttribute($name)
                if ($old -in @('1','true')) { $changes[$name] = $old; $nodes[0].SetAttribute($name,'0') }
            }
        }
    } finally {
        if ($null -ne $reader) { $reader.Dispose() }; if ($null -ne $stream) { $stream.Dispose() }
        if ($null -ne $archive) { $archive.Dispose() }
    }
    if ($changes.Count -eq 0) { return @{path=$source; changed_attributes=$changes; performed=$false; sha256=(Engine-Hash $source)} }
    $prepared = [IO.Path]::Combine([IO.Path]::GetDirectoryName($source),'calculation-input.xlsx')
    Assert-RegularEnginePath $prepared
    [IO.File]::Copy($source,$prepared,$false)
    $archive = $null; $stream = $null
    try {
        $archive = [IO.Compression.ZipFile]::Open($prepared,[IO.Compression.ZipArchiveMode]::Update)
        $archive.GetEntry('xl/workbook.xml').Delete()
        $entry = $archive.CreateEntry('xl/workbook.xml')
        $stream = $entry.Open(); $document.Save($stream)
    } finally { if ($null -ne $stream) { $stream.Dispose() }; if ($null -ne $archive) { $archive.Dispose() } }
    Assert-RegularEnginePath $prepared
    if ((Get-Item -LiteralPath $prepared).Length -gt 67108864) { throw 'EngineCalculationInputConflict' }
    return @{path=$prepared; changed_attributes=$changes; performed=$true; sha256=(Engine-Hash $prepared)}
}
Add-Type -TypeDefinition @'
using System;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;
public sealed class CivilExcelOwner : IDisposable {
  [StructLayout(LayoutKind.Sequential)] struct BasicLimit { public long ProcessTime, JobTime; public uint Flags; public UIntPtr Min, Max; public uint Active; public UIntPtr Affinity; public uint Priority, Scheduling; }
  [StructLayout(LayoutKind.Sequential)] struct IoCounters { public ulong ReadOps, WriteOps, OtherOps, ReadBytes, WriteBytes, OtherBytes; }
  [StructLayout(LayoutKind.Sequential)] struct ExtendedLimit { public BasicLimit Basic; public IoCounters Io; public UIntPtr ProcessMemory, JobMemory, PeakProcessMemory, PeakJobMemory; }
  [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr window, out uint pid);
  [DllImport("kernel32.dll", SetLastError=true)] static extern IntPtr OpenProcess(uint access, bool inherit, uint pid);
  [DllImport("kernel32.dll", SetLastError=true)] static extern bool GetProcessTimes(IntPtr process, out long created, out long exited, out long kernel, out long user);
  [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern bool QueryFullProcessImageName(IntPtr process, uint flags, StringBuilder name, ref uint size);
  [DllImport("kernel32.dll", CharSet=CharSet.Unicode, SetLastError=true)] static extern IntPtr CreateJobObject(IntPtr attributes, string name);
  [DllImport("kernel32.dll", SetLastError=true)] static extern bool SetInformationJobObject(IntPtr job, int info, ref ExtendedLimit value, uint size);
  [DllImport("kernel32.dll", SetLastError=true)] static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
  [DllImport("kernel32.dll", SetLastError=true)] static extern bool TerminateJobObject(IntPtr job, uint exit);
  [DllImport("kernel32.dll", SetLastError=true)] static extern bool TerminateProcess(IntPtr process, uint exit);
  [DllImport("kernel32.dll")] static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);
  [DllImport("kernel32.dll")] static extern bool CloseHandle(IntPtr handle);
  IntPtr process, job; bool assigned;
  public uint Pid { get; private set; } public long Created { get; private set; }
  public string Image { get; private set; } public bool Exited { get; private set; }
  public bool Forced { get; private set; }
  public CivilExcelOwner(IntPtr hwnd, int[] existing) {
    uint pid; GetWindowThreadProcessId(hwnd, out pid);
    if (pid == 0 || Array.IndexOf(existing, (int)pid) >= 0) throw new InvalidOperationException("ExistingOrUnknownExcelInstance");
    process = OpenProcess(0x100000 | 0x1000 | 0x100 | 1, false, pid);
    if (process == IntPtr.Zero) throw new Win32Exception();
    try {
      long created, exit, kernel, user;
      if (!GetProcessTimes(process, out created, out exit, out kernel, out user)) throw new Win32Exception();
      uint size=32768; var image=new StringBuilder((int)size);
      if (!QueryFullProcessImageName(process, 0, image, ref size)) throw new Win32Exception();
      Image=image.ToString(); if (!String.Equals(System.IO.Path.GetFileName(Image), "EXCEL.EXE", StringComparison.OrdinalIgnoreCase)) throw new InvalidOperationException("UnexpectedOfficeEngine");
      Pid=pid; Created=created;
      job=CreateJobObject(IntPtr.Zero, null); if (job == IntPtr.Zero) throw new Win32Exception();
      var limit=new ExtendedLimit(); limit.Basic.Flags=0x2000;
      if (!SetInformationJobObject(job, 9, ref limit, (uint)Marshal.SizeOf(typeof(ExtendedLimit)))) throw new Win32Exception();
      if (!AssignProcessToJobObject(job, process)) throw new Win32Exception();
      assigned=true;
    } catch { Dispose(); throw; }
  }
  public void Dispose() {
    if (process != IntPtr.Zero) {
      if (WaitForSingleObject(process, 2000) != 0) {
        Forced=true;
        if (assigned) TerminateJobObject(job, 2); else if (Pid != 0) TerminateProcess(process, 2);
      }
      Exited=WaitForSingleObject(process, 5000)==0;
      CloseHandle(process); process=IntPtr.Zero;
    }
    if (job != IntPtr.Zero) { CloseHandle(job); job=IntPtr.Zero; }
  }
}
'@
$request = $null; $application = $null; $workbooks = $null; $workbook = $null; $worksheets = $null; $functionObject = $null; $owner = $null
$receipt = $null; $failure = $null; $cleanupVerified = $false; $operationStage = 'request'; $failureHresult = $null
try {
    $line = [Console]::ReadLine()
    if ($null -eq $line -or $line.Length -gt 4194304) { throw 'InvalidEngineRequestSize' }
    $request = $line | ConvertFrom-Json
    $allowed = @('schema_version','job_id','source','output','expected_sha256','formula_cells','sheet_count','profile')
    if (@($request.PSObject.Properties.Name | Where-Object { $_ -notin $allowed }).Count -ne 0) { throw 'UnknownEngineRequestField' }
    if ($request.schema_version -ne 1 -or $request.profile -ne 'civil-xlsx-engine-v1' -or $request.job_id -notmatch '^[a-f0-9]{32}$' -or $request.expected_sha256 -notmatch '^[a-f0-9]{64}$') { throw 'InvalidEngineRequest' }
    $source = Resolve-EngineDesktopPath ([string]$request.source); $output = Resolve-EngineDesktopPath ([string]$request.output)
    Assert-RegularEnginePath $source; Assert-RegularEnginePath $output
    if ([IO.Path]::GetFileName($source) -ne 'input.xlsx' -or [IO.Path]::GetFileName($output) -ne 'output.xlsx' -or [IO.Path]::GetDirectoryName($source) -ne [IO.Path]::GetDirectoryName($output) -or -not (Test-Path -LiteralPath $source -PathType Leaf) -or (Test-Path -LiteralPath $output)) { throw 'InvalidEngineStagingPaths' }
    if ((Get-Item -LiteralPath $source).Length -gt 67108864 -or (Engine-Hash $source) -ne $request.expected_sha256) { throw 'EngineInputConflict' }
    $cells = @($request.formula_cells)
    if ($cells.Count -gt 20000 -or $request.sheet_count -lt 1 -or $request.sheet_count -gt 128) { throw 'EngineWorkbookLimit' }
    $locations = New-Object 'System.Collections.Generic.HashSet[string]'
    foreach ($cell in $cells) {
        if (@($cell.PSObject.Properties.Name | Where-Object { $_ -notin @('sheet','cell','formula') }).Count -ne 0 -or [string]$cell.sheet -eq '' -or ([string]$cell.sheet).Length -gt 31 -or $cell.cell -notmatch '^[A-Z]{1,3}[1-9][0-9]{0,6}$' -or -not ([string]$cell.formula).StartsWith('=') -or ([string]$cell.formula).Length -gt 8193 -or -not $locations.Add(([string]$cell.sheet) + "`0" + ([string]$cell.cell))) { throw 'InvalidEngineFormulaLocation' }
    }
    $operationStage = 'prepare_calculation_input'
    $calculationInput = New-EngineCalculationInput $source
    if ((Engine-Hash $source) -ne $request.expected_sha256) { throw 'EngineInputConflict' }
    $existingExcel = [int[]]@(Get-Process EXCEL -ErrorAction SilentlyContinue | ForEach-Object { $_.Id })
    $operationStage = 'activation'
    $application = New-Object -ComObject Excel.Application
    $owner = [CivilExcelOwner]::new([IntPtr]$application.Hwnd, [int[]]$existingExcel)
    $application.Visible = $false; $application.DisplayAlerts = $false; $application.EnableEvents = $false
    $application.AutomationSecurity = 3; $application.AskToUpdateLinks = $false; $application.IgnoreRemoteRequests = $true
    $workbooks = $application.Workbooks
    if ($workbooks.Count -ne 0) { throw 'UnexpectedStartupWorkbook' }
    Emit-EngineMessage @{ stage='owned'; job_id=$request.job_id; owned_pid=$owner.Pid; owned_process_creation_ticks=$owner.Created }
    $approvalLine = [Console]::ReadLine()
    if ($null -eq $approvalLine -or $approvalLine.Length -gt 1024) { throw 'MissingEngineOwnershipApproval' }
    $approval = $approvalLine | ConvertFrom-Json
    if (@($approval.PSObject.Properties.Name | Where-Object { $_ -notin @('job_id','continue') }).Count -ne 0 -or $approval.job_id -ne $request.job_id -or $approval.continue -ne $true) { throw 'InvalidEngineOwnershipApproval' }
    if ((Engine-Hash $source) -ne $request.expected_sha256) { throw 'EngineInputConflict' }
    $operationStage = 'open'
    # Use the COM optional defaults: AddToMru=false and CorruptLoad=xlNormalLoad.
    # Passing Type.Missing through Windows PowerShell for every optional slot is
    # rejected by installed Excel versions. Links=0 and ReadOnly=true are explicit.
    if ((Engine-Hash $calculationInput.path) -ne $calculationInput.sha256) { throw 'EngineCalculationInputConflict' }
    $workbook = $workbooks.Open($calculationInput.path, 0, $true)
    if ($workbooks.Count -ne 1) { throw 'UnexpectedOpenedWorkbook' }
    $worksheets = $workbook.Worksheets
    if ($worksheets.Count -ne $request.sheet_count) { throw 'EngineSheetCountConflict' }
    $operationStage = 'calculation'
    $application.Calculation = -4135
    $application.CalculateFullRebuild()
    $calculationDeadline = [DateTime]::UtcNow.AddSeconds(60)
    while ($application.CalculationState -ne 0) {
        if ([DateTime]::UtcNow -gt $calculationDeadline) { throw 'ExcelCalculationTimeout' }
        Start-Sleep -Milliseconds 50
    }
    $operationStage = 'capture'
    $functionObject = $application.WorksheetFunction
    $results = New-Object 'System.Collections.Generic.List[object]'
    foreach ($cell in $cells) {
        $sheet = $null; $range = $null
        try {
            $sheet = $worksheets.Item([string]$cell.sheet); $range = $sheet.Range([string]$cell.cell)
            if ($range.HasFormula -ne $true -or [string]$range.Formula -ne [string]$cell.formula) { throw 'EngineFormulaConflict' }
            if ($functionObject.IsError($range)) { throw 'ExcelFormulaError' }
            $value = $range.Value2; $type = 'number'
            if ($value -is [bool]) { $type = 'boolean' }
            elseif ($value -is [string]) { $type = 'text' }
            elseif ($null -eq $value -or $value -is [Array] -or [double]::IsNaN([double]$value) -or [double]::IsInfinity([double]$value)) { throw 'InvalidExcelFormulaValue' }
            $results.Add(@{ sheet=[string]$cell.sheet; cell=[string]$cell.cell; type=$type; value=$value })
        } finally { Release-EngineCom $range; Release-EngineCom $sheet }
    }
    $operationStage = 'save'
    $workbook.SaveCopyAs($output)
    Assert-RegularEnginePath $output
    if (-not (Test-Path -LiteralPath $output -PathType Leaf) -or (Engine-Hash $source) -ne $request.expected_sha256 -or (Engine-Hash $calculationInput.path) -ne $calculationInput.sha256 -or (Get-Item -LiteralPath $output).Length -gt 67108864) { throw 'EngineOutputConflict' }
    $receipt = @{ schema_version=1; engine='Microsoft Excel'; engine_version=[string]$application.Version; engine_build=[string]$application.Build;
        calculation_version=[int]$application.CalculationVersion; calculation_method='CalculateFullRebuild'; calculation_state='done';
        source_sha256=$request.expected_sha256; output_sha256=(Engine-Hash $output); owned_instance=$true; workbook_count=1;
        formula_results=@($results.ToArray()); job_id=$request.job_id; owned_pid=$owner.Pid; owned_process_creation_ticks=$owner.Created;
        calculation_input_sha256=$calculationInput.sha256; calculation_control_normalization=@{performed=$calculationInput.performed; changed_attributes=$calculationInput.changed_attributes; retained_input_sha256=$request.expected_sha256} }
} catch {
    # Fixed error codes only; never emit raw COM messages or document contents.
    $known = @('InvalidEnginePath','LinkedEnginePath','InvalidEngineRequestSize','UnknownEngineRequestField','InvalidEngineRequest','InvalidEngineStagingPaths','EngineInputConflict','EngineCalculationInputConflict','EngineWorkbookLimit','InvalidEngineFormulaLocation','UnexpectedStartupWorkbook','MissingEngineOwnershipApproval','InvalidEngineOwnershipApproval','UnexpectedOpenedWorkbook','EngineSheetCountConflict','ExcelCalculationTimeout','EngineFormulaConflict','ExcelFormulaError','InvalidExcelFormulaValue','EngineOutputConflict')
    $message = $_.Exception.Message
    $failure = if ($message -in $known) { $message } else { 'ExcelDesktopEngineFailed' }
    $exception = $_.Exception
    while ($null -ne $exception.InnerException) { $exception = $exception.InnerException }
    $failureHresult = $exception.HResult
} finally {
    if ($null -ne $owner) {
        try { if ($null -ne $workbook) { $workbook.Close($false) } } catch { if ($null -eq $failure) { $failure='ExcelCloseFailed' } }
        try { if ($null -ne $application) { $application.Quit() } } catch { if ($null -eq $failure) { $failure='ExcelQuitFailed' } }
    }
    Release-EngineCom $functionObject; Release-EngineCom $worksheets; Release-EngineCom $workbook; Release-EngineCom $workbooks; Release-EngineCom $application
    if ($null -ne $owner) { $owner.Dispose(); $cleanupVerified=$owner.Exited }
}
if ($null -eq $failure -and $null -ne $receipt -and $cleanupVerified) {
    $receipt.cleanup_verified=$true; $receipt.cleanup_method=if ($owner.Forced) { 'owned_job_termination' } else { 'graceful' }
    Emit-EngineMessage @{ stage='completed'; job_id=$request.job_id; receipt=$receipt }
    exit 0
}
if ($null -eq $failure) { $failure='ExcelCleanupUnverified' }
Emit-EngineMessage @{ stage='failed'; job_id=if ($null -ne $request) { $request.job_id } else { $null }; error=@{code=$failure; operation=$operationStage; hresult=$failureHresult; message='The local Excel operation did not complete verification; no output is approved.'}; cleanup_verified=$cleanupVerified }
exit 2
