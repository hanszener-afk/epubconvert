"""Format-neutral recognition of chapter headings and section names."""

from __future__ import annotations

import re

from novel2epub.ir import SectionKind

NUMBER_WORDS = [
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
    "twenty",
    "thirty",
    "forty",
    "fifty",
    "sixty",
    "seventy",
    "eighty",
    "ninety",
    "hundred",
]
_WORDS = "|".join(NUMBER_WORDS)
_NUM = rf"(\d{{1,3}}|[ivxlcdm]{{1,7}}|(?:{_WORDS})(?:[- ](?:{_WORDS}))*)"
CHAPTER_PATTERN = re.compile(rf"^(chapter|chap\.)\s+{_NUM}\b", re.IGNORECASE)
BARE_NUMBER = re.compile(r"^(\d{1,3}|[IVXLC]{1,7})\.?$")
BARE_WORD_NUMBER = re.compile(
    rf"^(?:{'|'.join(NUMBER_WORDS)})(?:[- ](?:{'|'.join(NUMBER_WORDS)}))*$", re.IGNORECASE
)

SECTION_NAMES: dict[str, SectionKind | None] = {
    "prologue": SectionKind.PROLOGUE,
    "epilogue": SectionKind.EPILOGUE,
    "dedication": SectionKind.DEDICATION,
    "epigraph": SectionKind.EPIGRAPH,
    "foreword": SectionKind.FOREWORD,
    "preface": SectionKind.PREFACE,
    "introduction": SectionKind.INTRODUCTION,
    "acknowledgements": SectionKind.ACKNOWLEDGEMENTS,
    "acknowledgments": SectionKind.ACKNOWLEDGEMENTS,
    "afterword": SectionKind.AFTERWORD,
    "about the author": SectionKind.ABOUT_AUTHOR,
    "copyright": SectionKind.COPYRIGHT,
    "contents": None,  # a typed table of contents: replaced by the generated one
    "table of contents": None,
}
STORY = {SectionKind.CHAPTER, SectionKind.PROLOGUE, SectionKind.EPILOGUE}
BACK = {
    SectionKind.ACKNOWLEDGEMENTS, SectionKind.AFTERWORD, SectionKind.ABOUT_AUTHOR,
    SectionKind.ALSO_BY, SectionKind.OTHER,
}  # fmt: skip
COPYRIGHT_RE = re.compile(r"\u00a9|\bcopyright\b|all rights reserved|\bISBN\b", re.IGNORECASE)


def norm_name(text: str) -> str:
    t = re.sub(r"[\s\u00a0]+", " ", text).strip().lower().rstrip(".:")
    return t.replace("\u2019", "'")


def named_kind(title: str) -> tuple[bool, SectionKind | None]:
    """(is a known name, kind). Kind None with True means a typed contents list."""
    name = norm_name(title)
    if name in SECTION_NAMES:
        return True, SECTION_NAMES[name]
    for prefix, kind in (("prologue", SectionKind.PROLOGUE), ("epilogue", SectionKind.EPILOGUE)):
        if re.match(rf"{prefix}\b\s*[:.\u2014\u2013-]", name):
            return True, kind  # "Prologue: The Man"
    if name.startswith("also by"):
        return True, SectionKind.ALSO_BY
    if name in (
        "author's note",
        "a note from the author",
        "note from the author",
        "author's afterword",
    ):
        return True, SectionKind.OTHER
    return False, SectionKind.CHAPTER
