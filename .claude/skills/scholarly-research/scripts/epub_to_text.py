#!/usr/bin/env python3
"""Convert an EPUB to plain text, one file per spine item plus a combined _FULL.txt.

Usage: python3 epub_to_text.py book.epub outdir/

Uses only the standard library. Headings become Markdown-style '#' lines, so
`grep -n '^#' outdir/_FULL.txt` gives a table of contents. Spine order is preserved,
so file numbering follows reading order. Footnote markers are kept as ^n.

Print page numbers are not in an EPUB. If the book has a subject index (usually one
of the last spine items), it carries the print pagination for every topic and name;
grep it for the terms you need to cite.
"""
import os
import re
import sys
import zipfile
from html.parser import HTMLParser
from xml.etree import ElementTree as ET


class TextExtractor(HTMLParser):
    BLOCK = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "li", "br", "tr",
             "blockquote", "section", "article", "aside", "header", "footer", "hr",
             "table", "dt", "dd", "figcaption"}

    def __init__(self):
        super().__init__()
        self.out = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in ("style", "script", "head"):
            self.skip += 1
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            self.out.append("\n\n" + "#" * int(tag[1]) + " ")
        elif tag in self.BLOCK:
            self.out.append("\n")
        if tag == "sup":
            self.out.append("^")

    def handle_endtag(self, tag):
        if tag in ("style", "script", "head"):
            self.skip -= 1
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6") or tag in self.BLOCK:
            self.out.append("\n")

    def handle_data(self, data):
        if not self.skip:
            self.out.append(data)

    def text(self):
        t = "".join(self.out)
        t = re.sub(r"[ \t\r\f\v]+", " ", t)
        t = re.sub(r"\n\s*\n\s*\n+", "\n\n", t)
        return t.strip()


def extract(path, outdir):
    os.makedirs(outdir, exist_ok=True)
    z = zipfile.ZipFile(path)
    container = ET.fromstring(z.read("META-INF/container.xml"))
    ns = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
    opf_path = container.find(".//c:rootfile", ns).attrib["full-path"]
    opf = ET.fromstring(z.read(opf_path))
    base = os.path.dirname(opf_path)
    ons = {"o": "http://www.idpf.org/2007/opf"}
    manifest = {i.attrib["id"]: i.attrib["href"] for i in opf.find("o:manifest", ons)}
    spine = [i.attrib["idref"] for i in opf.find("o:spine", ons)]
    full = []
    for n, idref in enumerate(spine):
        href = manifest[idref]
        p = os.path.join(base, href) if base else href
        try:
            raw = z.read(p).decode("utf-8", errors="ignore")
        except KeyError:
            continue
        te = TextExtractor()
        te.feed(raw)
        t = te.text()
        fn = f"{n:03d}_{os.path.basename(href).rsplit('.', 1)[0]}.txt"
        with open(os.path.join(outdir, fn), "w") as fh:
            fh.write(t)
        full.append(f"\n\n===== FILE {fn} =====\n\n" + t)
        print(f"{fn:45s} {len(t.split()):7d} words | {t[:70].replace(chr(10), ' ')}")
    with open(os.path.join(outdir, "_FULL.txt"), "w") as fh:
        fh.write("".join(full))
    print(f"\n{path} -> {outdir}: {len(spine)} spine items; combined text in _FULL.txt")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    extract(sys.argv[1], sys.argv[2])
