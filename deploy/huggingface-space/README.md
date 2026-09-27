# Hugging Face static showcase

`index.html` is the self-contained source for the public
[Civil Buddy SME showcase](https://huggingface.co/spaces/Niki68868/civil-buddy-sme).
It presents the workflow and recorded synthetic results. It does not run Python,
call a model, or process uploads.

The page retains the recorded example numbers and labels both container-count
and gross-mass statements **Partial**. The result card explains that A-frame
stillages are not modelled, gross mass uses approximate container tare and
excludes dunnage, lashing and stillage weight, and the 20,000 kg limit belongs to
the synthetic tender. A competent person must review and sign off the loading
plan before booking; the signed verified gross mass (VGM) governs.

## Publish the change

Pushing this directory to GitHub does **not** automatically update Hugging Face.
A person with write access to `Niki68868/civil-buddy-sme` must:

1. Open the Space's **Files** tab and replace its root `index.html` with this file.
2. Commit the change. Keep the Space's existing root `README.md` and `style.css`;
   this README is the GitHub publishing guide, not the Space configuration.
3. Once the Space shows **Running**, refresh the App and verify that both result
   labels include **Partial** and the **Calculation limits** note appears below
   the numbers. Confirm the source and synthetic-input links still work.

The baseline page was read from Hugging Face commit
`97f39af9bde999fa2561004ab79fd5d94760457a`. The wording follows the limitations in
the [repository overview](../../README.md#try-it-one-command-offline-no-key) and
[synthetic example notes](../../examples/facade-demo/README.md). If the Space has
changed since that baseline, review its newer edits before replacing the file.
