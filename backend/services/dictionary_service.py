"""The offline synonym dictionary (#52): Open English WordNet 2025, core edition.

Nothing of the data is in the repo. Turning on Settings → UI Behavior → Synonym
suggestions asks for an install: the official JSON zip is fetched once with a pinned
SHA-256 (`backend/downloads.py`), converted here into one small SQLite file, and the
zip is deleted. Lookups read that file with stdlib `sqlite3`, one read-only
connection per call.

The install is **not** a `BackgroundJob`. The job queue runs one job at a time, and
a 10 MB download that waited behind a two-hour scoring run would make the toggle look
broken (the user's ruling, 2026-11-14). It is one module-level task instead, whose
progress Settings polls through `GET /dictionary/status`. `docs/dev/dictionary.md`
has the why of each part.

Licence: CC BY 4.0 on top of Princeton's WordNet licence. Every copy keeps both
notices, so the install copies `backend/resources/wordnet/` next to the converted
file and writes a NOTICE saying what the conversion changed.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import shutil
import sqlite3
import threading
import unicodedata
import zipfile
from dataclasses import dataclass
from pathlib import Path
from urllib.error import URLError

from backend.config import settings
from backend.downloads import DownloadCancelled, DownloadHashMismatch, download_verified, part_path

logger = logging.getLogger(__name__)

EDITION = "Open English WordNet 2025 (core edition)"
ZIP_URL = (
    "https://github.com/globalwordnet/english-wordnet/releases/download/"
    "2025-edition/english-wordnet-2025-json.zip"
)
# Published by GitHub for the release asset; en-word.net serves the same bytes.
ZIP_SHA256 = "7d749f6e2c39e6970e4997839dcf6e42fd281f3c2fae0171d2192bae8cfa4b51"
ZIP_NAME = "english-wordnet-2025-json.zip"
DB_NAME = "oewn-2025.sqlite"
SCHEMA_VERSION = "1"

# The archive is 73 flat JSON files, 72.4 MB unpacked. The hash pin already makes the
# bytes trusted; these are a cheap second guard against a zip-slip name or a bomb.
_MEMBER_RE = re.compile(r"(entries-[0-9a-z]|frames|(noun|verb|adj|adv)\.[A-Za-z]+)\.json")
_MAX_UNPACKED = 300 * 1024 * 1024

_LICENCE_DIR = Path(__file__).resolve().parents[1] / "resources" / "wordnet"
_LICENCE_FILES = ("LICENSE.md", "WNDB_License.txt")

MAX_WORDS = 4
MAX_CHARS = 40
MAX_SENSES = 8
MAX_WORDS_PER_SENSE = 12


class DictionaryError(Exception):
    """The archive or the converted file is not what the converter expects."""


class NotInstalled(Exception):
    pass


def dictionary_dir() -> Path:
    # Read at call time, so a test's patched `settings.data_dir` is honoured.
    return settings.data_dir / "dictionary"


def db_path() -> Path:
    return dictionary_dir() / DB_NAME


def _tmp_db_path() -> Path:
    return dictionary_dir() / (DB_NAME + ".tmp")


# ── Install state ─────────────────────────────────────────────────────────────


@dataclass
class _Progress:
    state: str = "missing"  # downloading | converting | failed; missing/ready come from disk
    percent: float = -1.0
    message: str = ""
    error: str | None = None


_progress = _Progress()
_task: asyncio.Task | None = None
_stop = threading.Event()


def installed() -> bool:
    path = db_path()
    if not path.is_file():
        return False
    try:
        con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            row = con.execute("SELECT value FROM meta WHERE key = 'schema'").fetchone()
        finally:
            con.close()
    except sqlite3.Error:
        return False
    return row is not None and row[0] == SCHEMA_VERSION


def installing() -> bool:
    return _task is not None and not _task.done()


class Busy(Exception):
    """An install and a removal cannot overlap; the route answers 409."""


_removing = False


def status() -> dict:
    if installing():
        state, percent, message, error = _progress.state, _progress.percent, _progress.message, None
    elif installed():
        state, percent, message, error = "ready", 100.0, "", None
    elif _progress.state == "failed":
        state, percent, message, error = "failed", -1.0, "", _progress.error
    else:
        state, percent, message, error = "missing", -1.0, "", None
    size = None
    if state == "ready":
        try:
            size = db_path().stat().st_size
        except OSError:  # removed between the check and here
            state, size = "missing", None
    return {
        "installed": state == "ready",
        "state": state,
        "percent": percent,
        "message": message,
        "error": error,
        "size_bytes": size,
        "edition": EDITION,
    }


def start_install() -> dict:
    """Start the install, or join the one running. Ready already is a no-op."""
    global _task
    if _removing:
        raise Busy("The dictionary is being removed. Try again in a moment.")
    if installing() or installed():
        return status()
    _stop.clear()
    _progress.state, _progress.percent = "downloading", 0.0
    _progress.message, _progress.error = "Starting the download…", None
    _task = asyncio.get_running_loop().create_task(_install(), name="dictionary-install")
    return status()


# A stalled server holds the worker thread in `read()` for this long at most, and a
# shutdown that catches it there waits that long for the thread (the PM-027 class).
_READ_TIMEOUT = 20


def _install_sync(folder: Path) -> None:
    """Download, verify and convert, on a worker thread.

    The thread owns its own cleanup: a task cancel cannot stop it, so the asyncio side
    only asks it to stop (`_stop`) and waits. Cleanup here runs after the zip is
    closed, which is what Windows needs to delete it.
    """
    zip_path = folder / ZIP_NAME

    def _on_progress(done: int, total: int) -> None:
        _progress.percent = (done / total * 100.0) if total else -1.0
        _progress.message = f"Downloading ({done / 1_048_576:.1f} / {total / 1_048_576:.1f} MB)"

    try:
        download_verified(
            ZIP_URL, zip_path, ZIP_SHA256,
            on_progress=_on_progress, should_stop=_stop.is_set, timeout=_READ_TIMEOUT,
        )
        _progress.state, _progress.percent = "converting", -1.0
        _progress.message = "Building the dictionary…"
        build(zip_path, folder, _stop.is_set)
    finally:
        for leftover in (zip_path, _tmp_db_path()):
            try:
                leftover.unlink(missing_ok=True)
            except OSError as e:  # the startup sweep gets it next time
                logger.warning("Could not remove %s: %s", leftover, e)


async def _install() -> None:
    try:
        await asyncio.to_thread(_install_sync, dictionary_dir())
        logger.info("Dictionary installed at %s", db_path())
    except DownloadCancelled:
        _progress.state = "missing"
    except DownloadHashMismatch as e:
        logger.error("Dictionary download refused: %s", e)
        _fail(
            "The downloaded dictionary is not the expected file, so it was not used. "
            "Try again later."
        )
    # The message reaches the browser through `status()`, so it is a fixed string chosen
    # by the exception's type and never interpolates the exception: an OSError's text
    # names local paths, and a stray exception's text is anything at all. The log keeps
    # the detail, which is why each message points at Logs.
    except DictionaryError as e:
        logger.error("Dictionary conversion failed: %s", e)
        _fail("The downloaded dictionary is not in the expected format, so it was not used. "
              "See Logs for the details.")
    except (URLError, ConnectionError, TimeoutError) as e:  # URLError covers HTTPError
        logger.warning("Dictionary download failed: %s", e)
        _fail("Could not download the dictionary. Check the connection and retry.")
    except OSError as e:
        logger.warning("Dictionary install failed: %s", e)
        _fail("Could not save the dictionary. Check the free disk space, and see Logs "
              "for the details.")
    except Exception:  # never let the task die silently
        logger.exception("Dictionary install failed")
        _fail("Could not install the dictionary. See Logs for the details.")


def _fail(message: str) -> None:
    _progress.state, _progress.error = "failed", message


async def shutdown(grace: float = 5) -> None:
    """Stop an install in flight: ask the worker thread to stop, and wait for it.

    The thread checks `_stop` between download chunks and between archive members, so
    it normally ends within a second. A task cancel is the last resort, because it
    would end the coroutine while the thread still holds the zip open.
    """
    task = _task
    if task is None or task.done():
        return
    _stop.set()
    done, _ = await asyncio.wait({task}, timeout=grace)
    if not done:
        logger.warning("Dictionary install did not stop within %.0f s of shutdown", grace)
        task.cancel()


def sweep_leftovers() -> None:
    """Remove what an install the process did not live to finish left behind.
    Never fatal: a leftover the OS will not let go of waits for the next start."""
    folder = dictionary_dir()
    for leftover in (part_path(folder / ZIP_NAME), folder / ZIP_NAME, _tmp_db_path()):
        try:
            leftover.unlink(missing_ok=True)
        except OSError as e:
            logger.warning("Could not remove %s: %s", leftover, e)


async def remove() -> None:
    """Delete the installed dictionary.

    The database goes first, through `unlink_retrying`: on Windows a lookup or a status
    read holding it open blocks the delete, and `FileInUseError` becomes the 409 in
    `main.py`. Removing the folder first would delete the licence notices and then
    fail on the database, leaving an installed dictionary with its notices gone.
    """
    global _removing
    from backend.utils import unlink_retrying

    if installing():
        raise Busy("The dictionary is still installing.")
    _removing = True
    try:
        await unlink_retrying(db_path())
        _progress.state, _progress.error = "missing", None
        folder = dictionary_dir()
        if folder.exists():
            await asyncio.to_thread(shutil.rmtree, folder, True)  # ignore_errors: notices only
    finally:
        _removing = False


# ── Conversion ────────────────────────────────────────────────────────────────

_SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE synset (
    id TEXT PRIMARY KEY, pos TEXT NOT NULL, definition TEXT NOT NULL,
    members TEXT NOT NULL, similar TEXT NOT NULL
) WITHOUT ROWID;
CREATE TABLE sense (
    word TEXT NOT NULL, pos TEXT NOT NULL, spelling TEXT NOT NULL, rank INTEGER NOT NULL,
    synset_id TEXT NOT NULL
);
CREATE TABLE form (form TEXT NOT NULL, pos TEXT NOT NULL, lemma TEXT NOT NULL);
"""
_INDEXES = """
CREATE INDEX ix_sense_word ON sense (word, pos, rank);
CREATE INDEX ix_form_form ON form (form, pos);
"""


def _pos(key: str) -> str:
    """`n`, `v`, `a`, `r` from an entry's POS key (`n-1` marks a homograph) or a
    synset's `partOfSpeech` (`s`, an adjective satellite, is an adjective)."""
    p = key[:1]
    return "a" if p == "s" else p


def _key(word: str) -> str:
    return " ".join(word.replace("_", " ").lower().split())


def build(zip_path: Path, out_dir: Path, should_stop=lambda: False) -> Path:
    """Convert the OEWN JSON zip into `out_dir/oewn-2025.sqlite`, plus the notices.

    Reads one member at a time, so the 72 MB of JSON is never all in memory, and
    writes a `.tmp` file that is renamed into place last.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    tmp = out_dir / (DB_NAME + ".tmp")
    tmp.unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            infos = zf.infolist()
            for info in infos:
                if not _MEMBER_RE.fullmatch(info.filename):
                    raise DictionaryError(f"unexpected file in the archive: {info.filename!r}")
            if sum(i.file_size for i in infos) > _MAX_UNPACKED:
                raise DictionaryError("the archive unpacks to more than expected")
            names = [i.filename for i in infos]
            lexfiles = [n for n in names if not n.startswith(("entries-", "frames"))]
            entries = [n for n in names if n.startswith("entries-")]
            if not lexfiles or not entries:
                raise DictionaryError("the archive has no word or meaning files")

            con = sqlite3.connect(tmp)
            try:
                con.executescript(_SCHEMA)
                for name in lexfiles:
                    if should_stop():
                        raise DownloadCancelled(str(zip_path))
                    _load_synsets(con, json.loads(zf.read(name)))
                for name in entries:
                    if should_stop():
                        raise DownloadCancelled(str(zip_path))
                    _load_entries(con, json.loads(zf.read(name)))
                con.executescript(_INDEXES)
                con.executemany(
                    "INSERT INTO meta VALUES (?, ?)",
                    [("schema", SCHEMA_VERSION), ("edition", EDITION), ("source", ZIP_URL),
                     ("source_sha256", ZIP_SHA256)],
                )
                con.commit()
                con.execute("VACUUM")
            finally:
                con.close()
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    for name in _LICENCE_FILES:
        shutil.copyfile(_LICENCE_DIR / name, out_dir / name)
    (out_dir / "NOTICE.txt").write_text(_NOTICE, encoding="utf-8")
    final = out_dir / DB_NAME
    tmp.replace(final)
    return final


_NOTICE = f"""\
This folder holds a dictionary file built by Crucible from {EDITION},
downloaded from {ZIP_URL}

Open English WordNet is (c) 2019-present The Open English WordNet Team, licensed under
the Creative Commons Attribution 4.0 International License (LICENSE.md). It is derived
from Princeton WordNet, whose licence, notice and disclaimer are in WNDB_License.txt.

What Crucible changed: it keeps only each meaning's words, part of speech, first
definition and "similar" links, and each word's senses and irregular forms, and stores
them in the SQLite file {DB_NAME}. Nothing else was altered.
"""


def _load_synsets(con: sqlite3.Connection, synsets: dict) -> None:
    rows = []
    for sid, s in synsets.items():
        if not isinstance(s, dict):
            raise DictionaryError(f"meaning {sid!r} is not an object")
        definition = (s.get("definition") or [""])[0]
        rows.append((
            sid, _pos(s.get("partOfSpeech") or sid[-1:]), definition,
            json.dumps(s.get("members") or [], ensure_ascii=False),
            json.dumps(s.get("similar") or []),
        ))
    con.executemany("INSERT INTO synset VALUES (?, ?, ?, ?, ?)", rows)


def _load_entries(con: sqlite3.Connection, entries: dict) -> None:
    senses, forms = [], []
    for word, by_pos in entries.items():
        if not isinstance(by_pos, dict):
            raise DictionaryError(f"word {word!r} is not an object")
        key = _key(word)
        rank = 0
        for pos_key, entry in by_pos.items():
            pos = _pos(pos_key)
            for sense in entry.get("sense") or []:
                senses.append((key, pos, word, rank, sense["synset"]))
                rank += 1
            for form in entry.get("form") or []:
                forms.append((_key(form), pos, key))
    con.executemany("INSERT INTO sense VALUES (?, ?, ?, ?, ?)", senses)
    con.executemany("INSERT INTO form VALUES (?, ?, ?)", forms)


# ── Lookup ────────────────────────────────────────────────────────────────────

# WordNet's morphy rules (Princeton's morph.c): suffix → replacement, per POS.
_RULES = {
    "n": [("s", ""), ("ses", "s"), ("xes", "x"), ("zes", "z"), ("ches", "ch"),
          ("shes", "sh"), ("men", "man"), ("ies", "y")],
    "v": [("s", ""), ("ies", "y"), ("es", "e"), ("es", ""), ("ed", "e"), ("ed", ""),
          ("ing", "e"), ("ing", "")],
    "a": [("er", ""), ("est", ""), ("er", "e"), ("est", "e")],
    "r": [],
}


def normalize(word: str) -> str:
    """The lookup key for a selection: lowercase, `_` as a space, one space between
    words. Raises ValueError for something that is not a word or short phrase."""
    key = _key(word)
    if not key or len(key) > MAX_CHARS:
        raise ValueError(f"Select a word of 1 to {MAX_CHARS} letters.")
    if len(key.split(" ")) > MAX_WORDS:
        raise ValueError(f"Select at most {MAX_WORDS} words.")
    # A combining mark counts as part of a letter: `İ` lowercases to `i` + U+0307.
    if not all(c.isalpha() or c in " '-." or unicodedata.category(c).startswith("M") for c in key)             or not key[0].isalpha():
        raise ValueError("Select a word made of letters.")
    return key


def _known(con: sqlite3.Connection, word: str, pos: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sense WHERE word = ? AND pos = ? LIMIT 1", (word, pos)
    ).fetchone() is not None


def base_forms(con: sqlite3.Connection, key: str) -> list[tuple[str, str]]:
    """(lemma, pos) pairs `key` can be a form of, the word itself first.

    Morphy as Princeton and NLTK run it: an irregular form (the entries' `form`
    lists, WordNet's exception files folded in) is used alone when there is one;
    otherwise the regular suffix rules apply. Every candidate must be a known lemma.
    """
    out: list[tuple[str, str]] = []
    for pos in ("n", "v", "a", "r"):
        irregular = [
            lemma for (lemma,) in con.execute(
                "SELECT lemma FROM form WHERE form = ? AND pos = ?", (key, pos)
            )
        ]
        if irregular:
            candidates = [key, *irregular]
        else:
            candidates = [key] + [
                key[: -len(suffix)] + repl
                for suffix, repl in _RULES[pos]
                if key.endswith(suffix) and len(key) > len(suffix)
            ]
        for cand in candidates:
            if (cand, pos) not in out and _known(con, cand, pos):
                out.append((cand, pos))
    # The word itself first, whatever its POS, then its base forms.
    return sorted(out, key=lambda lp: lp[0] != key)


def synonyms(word: str) -> dict:
    """The meanings of `word` (or of its base forms) and other words for each."""
    key = normalize(word)
    typed = " ".join(word.replace("_", " ").split())
    path = db_path()
    if not installed():
        raise NotInstalled()
    con = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        pairs = base_forms(con, key)
        senses, seen = [], set()
        for lemma, pos in pairs:
            rows = con.execute(
                "SELECT s.id, s.pos, s.definition, s.members, s.similar FROM sense AS w "
                "JOIN synset AS s ON s.id = w.synset_id WHERE w.word = ? AND w.pos = ? "
                # `cat` and `CAT` share the key `cat` and each ranks from 0. The entry
                # spelled as the user typed it comes first (`CAT` in "CAT scan"), then
                # the lowercase one, then each entry in its own WordNet order.
                "ORDER BY w.spelling <> ?, w.spelling <> w.word, w.spelling, w.rank",
                (lemma, pos, typed),
            ).fetchall()
            for sid, spos, definition, members, similar in rows:
                if sid in seen:
                    continue
                seen.add(sid)
                words = _words(con, json.loads(members), json.loads(similar), spos, {key, lemma})
                if words:
                    senses.append({"lemma": lemma, "pos": spos, "definition": definition, "words": words})
        lemmas = list(dict.fromkeys(lemma for lemma, _ in pairs))
        return {"word": key, "lemmas": lemmas, "senses": senses[:MAX_SENSES]}
    finally:
        con.close()


def _words(con, members: list[str], similar: list[str], pos: str, exclude: set[str]) -> list[str]:
    """A meaning's other words. An adjective's live mostly in its `similar` meanings
    (a satellite points at its head, a head at its satellites), so those follow."""
    out = [m for m in members if _key(m) not in exclude]
    if pos == "a" and similar:
        marks = ",".join("?" * len(similar))
        found = dict(con.execute(f"SELECT id, members FROM synset WHERE id IN ({marks})", similar))
        for sid in similar:  # upstream's order, which IN does not keep
            out.extend(m for m in json.loads(found.get(sid, "[]")) if _key(m) not in exclude)
    return list(dict.fromkeys(out))[:MAX_WORDS_PER_SENSE]
