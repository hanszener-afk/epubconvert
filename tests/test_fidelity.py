from __future__ import annotations

import random
import time
import unicodedata
from pathlib import Path

from books import full_book, minimal_book
from hypothesis import given
from hypothesis import strategies as st

from novel2epub.diagnostics import Diagnostics
from novel2epub.epub.writer import write_epub
from novel2epub.verify.fidelity import compare, normalise, subtract_removals, tokens
from novel2epub.verify.text import epub_text, ir_text

# --- normalisation -------------------------------------------------------


def test_normalise_collapses_whitespace_and_strips() -> None:
    assert normalise("  a\t\tb\n\n c\u00a0\u2009d  ") == "a b c d"


def test_normalise_expands_ligatures_and_drops_invisibles() -> None:
    assert normalise("\ufb01ne \ufb02y o\ufb03ce") == "fine fly office"
    assert normalise("co\u00adoperate zero\u200bwidth\ufeff") == "cooperate zerowidth"


def test_normalise_keeps_typography() -> None:
    s = "\u201cHello\u2014there\u2026\u201d it\u2019s"
    assert normalise(s) == s


def test_normalise_composes_unicode() -> None:
    assert normalise("Cafe\u0301") == "Caf\u00e9"


@given(st.text())
def test_normalise_is_idempotent(s: str) -> None:
    once = normalise(s)
    assert normalise(once) == once


@given(st.text())
def test_normalise_has_no_edge_or_double_spaces(s: str) -> None:
    out = normalise(s)
    assert out == out.strip()
    assert "  " not in out
    assert all(not c.isspace() or c == " " for c in out)


LIGATURES = "\ufb00\ufb01\ufb02\ufb03\ufb04\ufb05\ufb06"


@given(
    st.text(alphabet=st.characters(categories=["L", "N", "P", "S"], exclude_characters=LIGATURES))
)
def test_normalise_preserves_visible_characters(s: str) -> None:
    # Without whitespace, ligatures, or invisibles, normalisation is exactly NFC.
    assert normalise(s) == unicodedata.normalize("NFC", s)


@given(st.lists(st.text(alphabet="abc \n\t", min_size=1), max_size=20))
def test_tokens_roundtrip_through_join(parts: list[str]) -> None:
    text = " ".join(parts)
    assert tokens(" ".join(tokens(text))) == tokens(text)


# --- comparison ----------------------------------------------------------


def test_identical_text_is_perfect() -> None:
    r = compare("One two three.", "One  two\nthree.")
    assert r.ratio == 1.0
    assert r.loss == 0.0
    assert r.missing == [] and r.added == []


def test_missing_text_is_reported_with_context() -> None:
    ref = "The cat sat on the mat. It was a very fine mat indeed. The end."
    out = "The cat sat on the mat. The end."
    r = compare(ref, out)
    assert r.loss > 0
    assert [h.text for h in r.missing] == ["It was a very fine mat indeed."]
    assert r.missing[0].before.endswith("the mat.")
    assert not r.passes(0.001)


def test_added_text_is_reported() -> None:
    r = compare("A B C", "A B extra C")
    assert [h.text for h in r.added] == ["extra"]
    assert r.loss == 0.0


def test_changed_word_counts_as_loss_and_addition() -> None:
    r = compare("he said quietly", "he said loudly")
    assert [h.text for h in r.missing] == ["quietly"]
    assert [h.text for h in r.added] == ["loudly"]


def test_threshold() -> None:
    ref = " ".join(f"w{i}" for i in range(2000))
    out = " ".join(f"w{i}" for i in range(2000) if i != 7)
    r = compare(ref, out)
    assert r.loss == 1 / 2000
    assert r.passes(0.001)
    assert not r.passes(0.0001)


def test_empty_reference() -> None:
    r = compare("", "")
    assert r.ratio == 1.0 and r.passes(0.001)


def test_novel_length_comparison_is_fast() -> None:
    # Zipf-like vocabulary: a few very common words, a long tail of rarer ones.
    rng = random.Random(1234)
    vocab = [f"w{i}" for i in range(8000)]
    weights = [1 / (i + 1) for i in range(len(vocab))]
    ref = " ".join(rng.choices(vocab, weights, k=150_000))
    out_tokens = ref.split()
    del out_tokens[70_000:70_050]
    start = time.perf_counter()
    r = compare(ref, " ".join(out_tokens))
    assert time.perf_counter() - start < 20
    assert sum(len(h.text.split()) for h in r.missing) == 50


# --- round trip through the writer ---------------------------------------


def test_full_book_text_survives_writer(tmp_path: Path) -> None:
    for book in (minimal_book(), full_book()):
        out = tmp_path / "book.epub"
        write_epub(book, out, Diagnostics())
        expected = ir_text(book)
        actual = epub_text(out)
        assert actual.main == expected.main
        assert actual.notes == expected.notes
        assert compare(expected.main, actual.main).ratio == 1.0


def test_ir_text_excludes_generated_text_and_includes_notes() -> None:
    t = ir_text(full_book())
    assert "* * *" not in t.main
    assert "Admiralty" in t.main
    assert "The Admiralty was in London." in t.notes
    assert "Nobody references this note." in t.notes
    assert "The Admiralty was in London." not in t.main
    assert "Chapter One: Arrival" in t.main


# --- removal subtraction -------------------------------------------------


def test_glued_removals_leave_no_space() -> None:
    text, unmatched = subtract_removals(
        "He does <i>not</i>. Fine.", [("<i>", "He does "), ("</i>", "He does not")]
    )
    assert text == "He does not. Fine."
    assert unmatched == []


def test_removal_prefers_matching_context() -> None:
    ref = "In 1840 she left. It began. 4 Then it ended."
    text, _ = subtract_removals(ref, [("4", "It began. ")])
    assert text == "In 1840 she left. It began. Then it ended."


def test_unlocatable_removal_is_reported() -> None:
    text, unmatched = subtract_removals("Nothing here.", [("absent", "")])
    assert text == "Nothing here."
    assert unmatched == ["absent"]


def test_removals_explain_missing_text() -> None:
    r = compare("A header line. The story.", "The story.", [("A header line.", "")])
    assert r.missing == [] and r.loss == 0.0
