from __future__ import annotations

from io import BytesIO

from .common import fail, fields, integer, number, text, validation


class PDF:
    def __init__(self, data):
        try:
            from pypdf import PdfReader
        except ImportError:
            fail("unavailable", "PDF operations require pypdf>=5")
        self.data = data
        try:
            self.reader = PdfReader(BytesIO(data), strict=True)
            if self.reader.is_encrypted:
                fail("unsupported", "Encrypted PDFs are not supported")
            if len(self.reader.pages) > 1000:
                fail("too_large", "PDF exceeds the 1000-page limit")
        except (ValueError, OSError) as exc:
            if hasattr(exc, "code"):
                raise
            fail("invalid_document", "Cannot read this PDF")

    def inspect(self):
        form = self.reader.get_fields() or {}
        return {"format": "pdf", "page_count": len(self.reader.pages),
                "pages": [{"page": i+1, "width": float(p.mediabox.width), "height": float(p.mediabox.height),
                           "rotation": p.rotation, "annotation_count": len(p.get("/Annots", []))}
                          for i, p in enumerate(self.reader.pages)],
                "fields": [{"name": str(name), "type": str(f.get("/FT", "")), "value": str(f.get("/V", ""))}
                           for name, f in form.items()],
                "validation": validation(), "ocr": "unavailable", "body_edit": "unsupported"}

    def read(self, args):
        pages = args.get("pages", list(range(1, min(len(self.reader.pages), 20)+1)))
        if not isinstance(pages, list) or len(pages) > 100:
            fail("invalid_request", "Read at most 100 pages")
        output, total = [], 0
        for index in pages:
            integer(index, 1, len(self.reader.pages))
            value = self.reader.pages[index-1].extract_text() or ""
            total += len(value)
            if total > 500_000:
                fail("too_large", "Read fewer PDF pages")
            output.append({"page": index, "text": value, "locator": {"page": index},
                           "text_layer": "present" if value.strip() else "empty_or_image",
                           "coordinates": "not_extracted"})
        return {"pages": output, "ocr": "not_performed"}

    def patch(self, patches):
        from pypdf import PdfWriter
        from pypdf.annotations import Text
        from pypdf.generic import ArrayObject, NameObject, TextStringObject

        form = self.reader.get_fields() or {}
        if any(f.get("/FT") == "/Sig" for f in form.values()):
            fail("unsupported", "Signed or signature-field PDFs require a signature-aware workflow")
        acroform = self.reader.trailer["/Root"].get("/AcroForm")
        if acroform is not None and "/XFA" in acroform.get_object():
            fail("unsupported", "XFA PDF forms require a different editor")
        writer = PdfWriter(clone_from=self.reader)
        changes = []
        for patch in patches:
            op = patch.get("op")
            if op == "reorder_pages":
                fields(patch, ("op", "pages"), ("evidence", "trust"))
                pages = patch["pages"]
                if not isinstance(pages, list) or any(type(n) is not int for n in pages) or sorted(pages) != list(range(1, len(writer.pages)+1)):
                    fail("invalid_patch", "reorder_pages requires a permutation of all 1-based page numbers")
                ordered = [writer.pages[i-1] for i in pages]
                node = writer.root_object["/Pages"]
                node[NameObject("/Kids")] = ArrayObject([p.indirect_reference for p in ordered])
                for page in ordered:
                    page[NameObject("/Parent")] = node.indirect_reference
                writer.flattened_pages = None
                changes.append({"op": op, "before": list(range(1, len(pages)+1)), "after": pages})
            elif op == "annotate":
                fields(patch, ("op", "page", "rect", "text"), ("author", "evidence", "trust"))
                index = integer(patch["page"], 1, len(writer.pages))
                rectangle = patch["rect"]
                if not isinstance(rectangle, list) or len(rectangle) != 4:
                    fail("invalid_patch", "Annotation rect requires four PDF point coordinates")
                x0, y0, x1, y1 = [number(n) for n in rectangle]
                bounds = writer.pages[index-1].mediabox
                if not float(bounds.left) <= x0 < x1 <= float(bounds.right) or not float(bounds.bottom) <= y0 < y1 <= float(bounds.top):
                    fail("invalid_patch", "Annotation rectangle must lie within the page mediabox")
                value = text(patch["text"], maximum=10_000)
                annotation = Text(rect=(x0, y0, x1, y1), text=value)
                annotation[NameObject("/T")] = TextStringObject(text(patch.get("author", "Civil Buddy"), maximum=200))
                writer.add_annotation(page_number=index-1, annotation=annotation)
                changes.append({"op": op, "page": index, "rect": rectangle, "after": value})
            elif op == "fill_fields":
                fields(patch, ("op", "fields"), ("evidence", "trust"))
                values = patch["fields"]
                if not isinstance(values, dict) or not values or len(values) > 200:
                    fail("invalid_patch", "Provide 1 to 200 named form fields")
                for name, value in values.items():
                    if name not in form:
                        fail("not_found", "PDF form field does not exist")
                    if form[name].get("/FT") != "/Tx" or int(form[name].get("/Ff", 0)) & 1:
                        fail("unsupported", "Only writable text form fields are supported")
                    text(value, maximum=min(int(form[name].get("/MaxLen", 10_000)), 10_000))
                writer.update_page_form_field_values(None, values, auto_regenerate=False)
                changes.append({"op": op, "before": {k: str(form[k].get("/V", "")) for k in values}, "after": values})
                for name, value in values.items():
                    form[name][NameObject("/V")] = TextStringObject(value)
            else:
                fail("unsupported", "PDF supports annotate, fill_fields and reorder_pages only")
            changes[-1].update(trust="model_proposed", evidence=patch.get("evidence", []))
        output = BytesIO()
        writer.write(output)
        # Content-stream text and annotations are re-read, never inferred from a successful write.
        reopened = PDF(output.getvalue())
        if len(reopened.reader.pages) != len(self.reader.pages):
            fail("validation_failed", "PDF page count changed unexpectedly")
        for change in changes:
            if change["op"] == "fill_fields":
                actual = reopened.reader.get_fields() or {}
                if any(str(actual.get(k, {}).get("/V", "")) != v for k, v in change["after"].items()):
                    fail("validation_failed", "PDF form values did not survive reopening")
        return output.getvalue(), changes, validation(visual="not_checked", form_appearance="not_rendered", body_edit="not_performed")
