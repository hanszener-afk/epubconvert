"""Synthetic HTML manuscripts imitating the sources the HTML reader supports.

They are written from the published structure of each source, not copied from
real exports; real samples should be added to local/ for testing when
available.
"""

from __future__ import annotations

CLAUDE = """<!DOCTYPE html>
<html lang="en-GB">
<head>
<meta charset="utf-8">
<title>The Harbour</title>
<meta name="author" content="Ann Author">
<style>
  body { font-family: Georgia, serif; max-width: 40em; margin: auto; }
  .scene { text-align: center; }
</style>
<script>console.log("not content");</script>
</head>
<body>
<header>
  <h1>The Harbour</h1>
</header>
<nav>
  <ol><li><a href="#c1">Chapter One</a></li><li><a href="#c2">Chapter Two</a></li></ol>
</nav>
<main>
<article>
  <h2 id="c1">Chapter One</h2>
  <p>The harbour was old, and the <em>Mary Rose</em> was older.</p>
  <p>Nobody   remembered
     who had built it.</p>
  <hr>
  <p>Morning came <strong>quickly</strong>.</p>
  <h2 id="c2">Chapter Two</h2>
  <p>The tide turned.<br>And turned again.</p>
  <blockquote><p>Keep the light burning.</p></blockquote>
  <p>She did.</p>
</article>
</main>
</body>
</html>
"""

GOOGLE_DOCS = """<html><head><meta content="text/html; charset=UTF-8" http-equiv="content-type">\
<style type="text/css">ol{margin:0;padding:0}.c1{font-style:italic;font-weight:400}\
.c2{font-weight:700}.c3{text-align:center;padding-top:0pt}.c4{font-weight:400}\
.c5{height:11pt}.title{font-size:26pt}h1{font-size:20pt}.c6{vertical-align:super}\
</style></head><body class="c9 doc-content">\
<p class="c3 title" id="h.t"><span class="c4">The Long Book</span></p>\
<h1 class="c3" id="h.a"><span class="c4">Chapter One</span></h1>\
<p class="c0"><span class="c4">It began with a </span><span class="c1">whisper</span>\
<span class="c4">, and a </span><span class="c2">shout</span><span class="c4">.</span>\
<sup><a href="#ftnt1" id="ftnt_ref1">[1]</a></sup></p>\
<p class="c0 c5"><span class="c4"></span></p>\
<p class="c0"><span class="c4">See </span><span class="c4"><a \
href="https://www.google.com/url?q=https://example.org/map&amp;sa=D&amp;ust=1">the map</a></span>\
<span class="c4">.</span><sup><a href="#cmnt1" id="cmnt_ref1">[a]</a></sup></p>\
<hr style="page-break-before:always;display:none;">\
<h1 class="c3" id="h.b"><span class="c4">Chapter Two</span></h1>\
<p class="c0"><span class="c4">It went on.</span></p>\
<p class="c3"><span class="c4">* * *</span></p>\
<p class="c0"><span class="c4">It ended quietly.</span></p>\
<hr class="c7"><div><p class="c0"><a href="#ftnt_ref1" id="ftnt1">[1]</a>\
<span class="c4">&nbsp;A note about whispers.</span></p></div>\
<div class="c8"><p class="c0"><a href="#cmnt_ref1" id="cmnt1">[a]</a>\
<span class="c4">Check this link before publishing.</span></p></div>\
</body></html>"""

AO3 = """<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8"/>
<title>Salt and Iron - Bea Writer - Original Work [Archive of Our Own]</title>
</head>
<body>
<div id="preface">
  <p class="message"><b>Salt and Iron</b><br/>Posted originally on the
  <a href="http://archiveofourown.org/">Archive of Our Own</a> at
  <a href="http://archiveofourown.org/works/1">http://archiveofourown.org/works/1</a>.</p>
  <div class="meta">
    <dl class="tags">
      <dt>Rating:</dt><dd><a href="http://archiveofourown.org/tags/General">General Audiences</a></dd>
      <dt>Language:</dt><dd>English</dd>
      <dt>Stats:</dt><dd>Published: 2026-01-01 Words: 120 Chapters: 2/2</dd>
    </dl>
    <h1>Salt and Iron</h1>
    <div class="byline">by <a rel="author" href="http://archiveofourown.org/users/bea">Bea Writer</a></div>
    <p>Summary</p>
    <blockquote class="userstuff"><p>A smith, a sea, a bargain.</p></blockquote>
    <p>Notes</p>
    <blockquote class="userstuff"><p>Written for a friend.</p></blockquote>
  </div>
</div>
<div id="chapters" class="userstuff">
  <div class="meta group">
    <h2 class="heading">Chapter 1: The Forge</h2>
  </div>
  <div class="userstuff">
    <h3 class="landmark heading">Chapter Text</h3>
    <p>The forge was cold when she found it.</p>
    <p><em>Not for long</em>, she thought.</p>
  </div>
  <div class="meta group">
    <h2 class="heading">Chapter 2: The Sea</h2>
  </div>
  <div class="userstuff">
    <h3 class="landmark heading">Chapter Text</h3>
    <p>The sea took everything back.</p>
  </div>
</div>
<div id="afterword">
  <div class="meta">
    <p>End Notes</p>
    <blockquote class="userstuff"><p>Thank you for reading.</p></blockquote>
  </div>
  <div id="endnotes">
    <p class="message">Please <a href="http://archiveofourown.org/works/1/comments/new">drop by the archive and comment</a> to let the creator know if you enjoyed their work!</p>
  </div>
</div>
</body>
</html>
"""

FOOTNOTES = """<html><head><title>Notes</title><meta name="author" content="Cid Scholar"></head>
<body>
<h1>Chapter One</h1>
<p>The harbour<sup class="footnote-ref"><a href="#fn1" id="fnref1">1</a></sup> was old.</p>
<p>A second note<sup><a href="#fn2" id="fnref2">*</a></sup> here.</p>
<h1>Chapter Two</h1>
<p>More text.</p>
<hr class="footnotes-sep">
<section class="footnotes"><ol class="footnotes-list">
<li id="fn1" class="footnote-item"><p>Rebuilt in 1850. <a href="#fnref1" class="footnote-backref">↩︎</a></p></li>
<li id="fn2" class="footnote-item"><p>A custom mark. <a href="#fnref2" class="footnote-backref">↩︎</a></p></li>
</ol></section>
</body></html>
"""
