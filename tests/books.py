"""Hand-built IR books used by the milestone 1 tests."""

from __future__ import annotations

import io
from datetime import UTC, datetime

from PIL import Image as PILImage

from novel2epub.ir import (
    Align,
    BlockQuote,
    Bold,
    Book,
    Cover,
    Epigraph,
    Heading,
    Image,
    Italic,
    LineBreak,
    Link,
    ManualRef,
    Metadata,
    Note,
    NoteKind,
    NoteRef,
    Paragraph,
    Resource,
    SceneBreak,
    Section,
    SectionKind,
    SmallCaps,
    Subscript,
    Superscript,
    Text,
    Verse,
)

MODIFIED = datetime(2026, 3, 14, 9, 26, 53, tzinfo=UTC)


def png(width: int, height: int, colour: tuple[int, int, int] = (90, 60, 30)) -> bytes:
    buf = io.BytesIO()
    PILImage.new("RGB", (width, height), colour).save(buf, format="PNG")
    return buf.getvalue()


def jpeg(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    PILImage.new("RGB", (width, height), (20, 40, 80)).save(buf, format="JPEG", quality=80)
    return buf.getvalue()


def p(*parts: str | object, align: Align = Align.START) -> Paragraph:
    content = [Text(x) if isinstance(x, str) else x for x in parts]
    return Paragraph(content, align=align, source=ManualRef("hand-built"))  # type: ignore[arg-type]


def minimal_book() -> Book:
    meta = Metadata(
        title="A Small Book",
        authors=["Ann Author"],
        language="en-GB",
        identifier="urn:uuid:7b0c9e7e-0d1f-4c55-9e43-2f6f2a1d3c11",
        modified=MODIFIED,
    )
    chapter = Section(
        SectionKind.CHAPTER,
        [Text("Chapter One")],
        [p("It was a dark night."), p("Then it rained.")],
    )
    return Book(metadata=meta, body=[chapter])


def full_book() -> Book:
    meta = Metadata(
        title="The Lighthouse Keeper\u2019s Daughter",
        subtitle="A Novel",
        authors=["Ann Author", "Bea Writer"],
        language="en-GB",
        identifier="urn:uuid:0e8a1a3c-55a4-4c7e-9a8d-6b4b52c3f0a2",
        modified=MODIFIED,
        publisher="Small Press",
        date="2026",
        series="Coastal Tales",
        series_index="2",
        description="A story about a lighthouse & the sea.",
        rights="Copyright \u00a9 2026 Ann Author. All rights reserved.",
        isbn="9780000000002",
    )
    resources = {
        "cover": Resource("cover", "image/jpeg", jpeg(1600, 2560)),
        "map": Resource("map", "image/png", png(400, 300)),
        "sketch": Resource("sketch", "image/png", png(200, 200, (10, 10, 10))),
    }
    front = [
        Section(
            SectionKind.TITLEPAGE,
            None,
            [
                p(Bold([Text("The Lighthouse Keeper\u2019s Daughter")]), align=Align.CENTRE),
                p("Ann Author and Bea Writer", align=Align.CENTRE),
            ],
        ),
        Section(
            SectionKind.COPYRIGHT,
            None,
            [p("Copyright \u00a9 2026 Ann Author."), p("First published 2026.")],
        ),
        Section(SectionKind.DEDICATION, None, [p(Italic([Text("For M.")]), align=Align.CENTRE)]),
        Section(
            SectionKind.EPIGRAPH,
            None,
            [
                Epigraph(
                    [p("The sea has neither meaning nor pity.")],
                    attribution=[Text("Anton Chekhov")],
                )
            ],
        ),
    ]
    ch1 = Section(
        SectionKind.CHAPTER,
        [Text("Chapter One: "), Italic([Text("Arrival")])],
        [
            p(
                "\u201cYou\u2019re late,\u201d she said\u2014not unkindly\u2026 ",
                Italic([Text("very")]),
                " late.",
            ),
            p(
                "The ",
                SmallCaps([Text("Admiralty")]),
                " notice was dated 3",
                Superscript([Text("rd")]),
                " March; the formula read H",
                Subscript([Text("2")]),
                "O.",
                NoteRef("fn1"),
            ),
            p("Non\u00a0breaking and a soft", LineBreak(), "line break."),
            SceneBreak(),
            p("Later, she read ", Link([Text("the notice")], href="https://example.org/"), "."),
            Heading([Text("The Letter")], level=2),
            p("Dear Sir,", NoteRef("en1")),
            BlockQuote([p("I regret to inform you."), p("Yours, &c.")]),
            p("She folded it away."),
        ],
    )
    ch2 = Section(
        SectionKind.CHAPTER,
        [Text("Chapter Two")],
        [
            Epigraph([p("Row, row, row your boat.")]),
            p("A song came over the water:", NoteRef("fn2")),
            Verse(
                [
                    [[Text("Gently down the stream,")], [Text("merrily, merrily,")]],
                    [[Italic([Text("life is but a dream.")])]],
                ]
            ),
            Image("map", alt="A map of the headland and the lighthouse."),
            Image("sketch", alt=None, caption=[Text("A sketch.")]),
            p("End of chapter.", NoteRef("fn1")),
        ],
    )
    back = [
        Section(SectionKind.ACKNOWLEDGEMENTS, [Text("Acknowledgements")], [p("Thanks to all.")]),
        Section(SectionKind.ABOUT_AUTHOR, [Text("About the Author")], [p("Ann lives by the sea.")]),
    ]
    notes = {
        "fn1": Note("fn1", NoteKind.FOOTNOTE, [p("The Admiralty was in London.")]),
        "fn2": Note("fn2", NoteKind.FOOTNOTE, [p("A traditional song."), p("Second para.")]),
        "en1": Note("en1", NoteKind.ENDNOTE, [p("The letter survives in the archive.")]),
        "orphan": Note("orphan", NoteKind.FOOTNOTE, [p("Nobody references this note.")]),
    }
    return Book(
        metadata=meta,
        front=front,
        body=[ch1, ch2],
        back=back,
        notes=notes,
        resources=resources,
        cover=Cover("cover", alt="Cover of The Lighthouse Keeper\u2019s Daughter"),
    )
