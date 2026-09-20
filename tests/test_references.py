"""A paper's reference list read by rules: entries split, each one's
surnames, year, title and ids, and a score against a library document."""

# ruff: noqa: E501 - the entries as the extractors wrote them, one line each

from __future__ import annotations

from prax import references as refs

IEEE = """- [1] D. Turnbull, L. Barrington, and G. Lanckriet, “Five approaches to collecting tags for music,” in _Proceedings of the 9th International Conference on Music Information Retrieval_ , (Philadelphia, USA), pp. 225–230, 2008.

- [2] P. Herrera, X. Serra, and G. Peeters, “A proposal for the description of audio in the context of MPEG-7,” 1999. MPEG.

- [4] E. Pampalk, “Islands of music: Analysis, organization, and visualization of music archives,” Master’s thesis,

Vienna University of Technology, Vienna, Austria, December 2001.

- [9] J. C. Brown, “Calculation of a constant Q spectral transform”, _J._ Acoust _. Soc. Am_ , 89(1), pp.425-434, Jan. 1990.
"""

APA = """- Boden, M. A. (1994b). What is creativity? In M. A. Boden (Ed.), _Dimensions of creativity_ (pp. 75–118). Cambridge, MA: MIT Press.

- Burghardt, M. D. (1995). _Introduction to the engineering profession_ (2nd ed.). New York: Addison-Wesley.
"""

ACM = """- JACOB, R. J. K., SIBERT, L. E., MCFARLANE, D. C., AND MULLEN, M. P. 1994. Integrality and separability of input devices. _ACM Trans. Comput. Hum. Interact. 1_ , 1 (Mar.), 3–26.

- KABBASH, P., BUXTON, W., AND SELLEN, A. 1994. Two-handed input in a compound task. In _Proceedings of the ACM Conference on Human Factors in Computing Systems_ (CHI ’94, Boston, MA, Apr. 24–28), B. Adelson, S. Dumais, and J. Olson, Eds. ACM Press, New York, NY, 417–423.
"""

SPRINGER = """_Sensors_ **2014** , _14_

**1948**

1. Lalegani Dezaki M, Mohd Ariffin MKA, Hatami S (2021) An overview of fused deposition modelling (FDM): research, development and process optimisation. Rapid Prototyp J 27(3):562–582

2. Siemiński P (2021) “Introduction to fused deposition modeling,.” Additive manufacturing. Elsevier, pp 217–275

9. Allen, M.; Girod, L.; Newton, R.; Madden, S.; Blumstein, D.T.; Estrin, D. VoxNet: An Interactive, Rapidly-Deployable Acoustic Monitoring Platform. In Proceedings of the IEEE International Conference on Information Processing in Sensor Networks (ipsn 2008), St. Louis, MO, USA, 22–24 April 2008; pp. 371–382.

10. Aarabi, P. The fusion of distributed microphone arrays for sound localization. _EURASIP J. Adv. Signal Process._ **2003** , _4_ , 338–347. https://doi.org/10.1155/S1110865703212014
"""


ELSEVIER = (
    "[G. Yu, M. Yu, C. Xu, Synchroextracting transform, IEEE Trans. Ind. Electron."
    " 64 \\(10\\) \\(2017\\) 8042–8054.](http://refhub.elsevier.com/S0888-3270(18)30390-X/h0095)"
    " [20] [K. Dragomiretskiy, D. Zosso, Variational mode decomposition, IEEE Trans."
    " Signal Process. 62 \\(3\\) \\(2014\\) 531–544.](http://refhub.elsevier.com/S0888-3270(18)30390-X/h0100)"
    " [21] [S.G. Mallat, Z. Zhang, Matching pursuits with time-frequency dictionaries,"
    " IEEE Trans. Signal Process. 41 \\(12\\) \\(1993\\) 3397–3415](http://refhub.elsevier.com/x)"
)


def test_elsevier_entries_run_together_in_one_paragraph_behind_links() -> None:
    e = refs.entries(ELSEVIER)
    assert len(e) == 3 and e[1].startswith("[20] K. Dragomiretskiy")
    a, b, c = (refs.parse(x) for x in e)
    assert a.title == "Synchroextracting transform" and a.year == 2017
    assert a.surnames == ["Yu", "Xu"]  # once each
    assert b.title == "Variational mode decomposition" and b.number == 20
    assert b.surnames == ["Dragomiretskiy", "Zosso"] and b.year == 2014
    assert c.title == "Matching pursuits with time-frequency dictionaries"
    assert c.surnames == ["Mallat", "Zhang"] and c.year == 1993


def test_a_suffix_in_the_author_run() -> None:
    r = refs.parse(
        "- 43. Välimäki, V.; Nam, J.; Smith, J.O., III; Abel, J.S. Alias-suppressed"
        " oscillators based on differentiated polynomial waveforms. *IEEE Trans.*"
        " **2010**, 18, 786–798."
    )
    assert (
        r.title
        == "Alias-suppressed oscillators based on differentiated polynomial waveforms"
    )
    assert r.surnames == ["Välimäki", "Nam", "Smith", "Abel"] and r.year == 2010


def test_entries_split_on_markers_and_join_continuations() -> None:
    e = refs.entries(IEEE)
    assert len(e) == 4
    assert e[2].startswith("- [4] E. Pampalk") and e[2].endswith("December 2001.")
    assert len(refs.entries(APA)) == 2 and len(refs.entries(ACM)) == 2
    e = refs.entries(SPRINGER)
    assert [x[:12] for x in e] == [
        "1. Lalegani ",
        "2. Siemiński",
        "9. Allen, M.",
        "10. Aarabi, ",
    ]
    assert refs.entries("") == []


def test_ieee_entries_take_the_quoted_title() -> None:
    a, b, c, d = (refs.parse(e) for e in refs.entries(IEEE))
    assert a.number == 1 and a.year == 2008
    assert a.title == "Five approaches to collecting tags for music"
    assert a.surnames == ["Turnbull", "Barrington", "Lanckriet"]
    assert b.title == "A proposal for the description of audio in the context of MPEG-7"
    assert b.year == 1999
    assert c.title == (
        "Islands of music: Analysis, organization, and visualization of music archives"
    )
    assert c.surnames == ["Pampalk"] and c.year == 2001
    assert d.title == "Calculation of a constant Q spectral transform"
    assert d.surnames == ["Brown"] and d.year == 1990 and d.number == 9


def test_author_year_entries_take_the_sentence_after_the_year() -> None:
    a, b = (refs.parse(e) for e in refs.entries(APA))
    assert a.surnames == ["Boden"] and a.year == 1994
    assert a.title == "What is creativity?"
    assert b.title == "Introduction to the engineering profession"
    a, b = (refs.parse(e) for e in refs.entries(ACM))
    assert a.surnames == ["Jacob", "Sibert", "Mcfarlane", "Mullen"] and a.year == 1994
    assert a.title == "Integrality and separability of input devices"
    assert b.title == "Two-handed input in a compound task"


def test_numbered_entries_without_quotes_take_the_sentence_after_the_authors() -> None:
    a, b, c, d = (refs.parse(e) for e in refs.entries(SPRINGER))
    assert a.number == 1 and a.year == 2021
    assert a.title.startswith("An overview of fused deposition modelling (FDM)")
    assert "Dezaki" in a.surnames and "Hatami" in a.surnames
    assert b.title == "Introduction to fused deposition modeling"
    assert c.number == 9 and c.year == 2008
    assert (
        c.title
        == "VoxNet: An Interactive, Rapidly-Deployable Acoustic Monitoring Platform"
    )
    assert c.surnames[:3] == ["Allen", "Girod", "Newton"]
    assert (
        d.title == "The fusion of distributed microphone arrays for sound localization"
    )
    assert d.doi == "10.1155/s1110865703212014" and d.year == 2003


def test_ids_are_read_when_printed() -> None:
    r = refs.parse(
        "[7] A. Vaswani et al., “Attention is all you need,” arXiv:1706.03762, 2017."
        " doi:10.48550/arXiv.1706.03762."
    )
    assert r.arxiv == "1706.03762" and r.doi == "10.48550/arxiv.1706.03762"
    assert r.title == "Attention is all you need" and r.surnames == ["Vaswani"]


def test_similarity_and_match() -> None:
    r = refs.parse(
        "[3] M. Plumbley, T. Blumensath, L. Daudet, R. Gribonval, and M. Davies, “Sparse"
        " Representations in Audio and Music: From Coding to Source Separation,”"
        " _Proceedings of the IEEE_, vol. 98, no. 6, pp. 995–1005, 2010."
    )
    same = refs.similarity(
        r,
        title="Sparse representations in audio and music: from coding to source separation",
        creators=["Mark D. Plumbley", "Thomas Blumensath"],
        year=2010,
    )
    assert same > 0.95
    # the extractor dropped the subtitle: still a match
    short = refs.similarity(r, title="Sparse Representations in Audio and Music")
    assert short >= refs.THRESHOLD
    other = refs.similarity(
        r, title="Sparse coding of audio signals for source separation", year=2004
    )
    assert other < refs.THRESHOLD
    assert refs.similarity(refs.Reference(raw=""), title="x") == 0.0
    assert refs.match([(1, same), (2, other)]) == ([1], "sure")
    assert refs.match([(1, 0.9), (2, 0.88), (3, 0.83)]) == ([1, 2], "ambiguous")
    assert refs.match([(1, 0.5)]) == ([], "none")
    assert refs.match([]) == ([], "none")
