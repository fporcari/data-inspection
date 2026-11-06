"""Pieces shared by the data-inspection scripts.

Presidio is optional and lives in a venv of its own, outside any checkout so every
agent and clone finds it: its cryptography pin cannot coexist with what a shared
Python already carries. A script started by a Python without Presidio re-runs
itself under that venv when it exists.
"""
import logging
import os
import pathlib
import re
import subprocess
import sys

try:
    from presidio_analyzer import AnalyzerEngine
    from presidio_analyzer.nlp_engine import NlpEngineProvider
except ImportError:
    AnalyzerEngine = None

VENV_PYTHON = pathlib.Path(os.environ.get(
    'DATA_INSPECTION_VENV', pathlib.Path.home() / '.local' / 'share' / 'data-inspection' / 'venv')
) / 'bin' / 'python'
REEXEC_FLAG = 'DATA_INSPECTION_REEXEC'
SPACY_MODEL = 'it_core_news_md'
MIN_SCORE = 0.6
NER_ENTITIES = {'PERSON', 'LOCATION', 'NRP', 'ORGANIZATION'}

INT = re.compile(r'^[+-]?\d+$')
DECIMAL = re.compile(r'^[+-]?\d+[.,]\d+$')
DATE = re.compile(r'^(\d{4}-\d{2}-\d{2}|\d{2}[/.-]\d{2}[/.-]\d{4})([ T]\d{2}:\d{2}(:\d{2})?.*)?$')
EMAIL_LIKE = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
CF = re.compile(r'^[A-Z]{6}[0-9LMNPQRSTUV]{2}[ABCDEHLMPRST][0-9LMNPQRSTUV]{2}'
                r'[A-Z][0-9LMNPQRSTUV]{3}[A-Z]$')
IBAN = re.compile(r'^[A-Z]{2}\d{2}[A-Z0-9]{11,30}$')
LETTERS = 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
CF_ODD = dict(zip('0123456789' + LETTERS,
                  [1, 0, 5, 7, 9, 13, 15, 17, 19, 21] * 2
                  + [2, 4, 18, 20, 11, 3, 6, 8, 12, 14, 16, 10, 22, 25, 24, 23]))
CF_EVEN = {**{d: int(d) for d in '0123456789'}, **{c: i for i, c in enumerate(LETTERS)}}
MAP_KEYS = 20
FIELD_NAME = re.compile(r'^[A-Za-z_$][\w$.\-]{0,63}$')


def is_map(value):
    """A dict keyed by data (tax ids, emails, codes, names), whose keys must never become paths."""
    return len(value) > MAP_KEYS or any(
        not FIELD_NAME.match(str(key)) or sum(c.isdigit() for c in str(key)) >= 3 for key in value)


def use_skill_venv(script):
    if AnalyzerEngine is None and VENV_PYTHON.exists() and not os.environ.get(REEXEC_FLAG):
        os.environ[REEXEC_FLAG] = '1'
        os.execv(VENV_PYTHON, [str(VENV_PYTHON), script, *sys.argv[1:]])


def load_analyzer():
    if AnalyzerEngine is None:
        return None
    logging.getLogger('presidio-analyzer').setLevel(logging.ERROR)
    try:
        nlp = NlpEngineProvider(nlp_configuration={
            'nlp_engine_name': 'spacy',
            'models': [{'lang_code': 'it', 'model_name': SPACY_MODEL}],
        }).create_engine()
    except OSError:
        return None
    return AnalyzerEngine(nlp_engine=nlp, supported_languages=['it'])


def detect(analyzer, text):
    """Entities found in text, without NER guesses overlapping a certain pattern match."""
    results = [r for r in analyzer.analyze(text=text, language='it') if r.score >= MIN_SCORE]
    certain = [r for r in results if r.entity_type not in NER_ENTITIES and r.score >= 0.9]
    return [r for r in results
            if not (r.entity_type in NER_ENTITIES
                    and any(c.start < r.end and r.start < c.end for c in certain))]


def classify(text):
    if not text:
        return 'empty'
    for name, pattern in (('int', INT), ('decimal', DECIMAL), ('date', DATE),
                          ('email-like', EMAIL_LIKE)):
        if pattern.match(text):
            return name
    return 'text'


def cf_check(body):
    return LETTERS[sum((CF_ODD if i % 2 == 0 else CF_EVEN)[c] for i, c in enumerate(body)) % 26]


def vat_check(digits):
    total = 0
    for i, digit in enumerate(digits):
        n = int(digit) * (2 if i % 2 else 1)
        total += n - 9 if n > 9 else n
    return str((10 - total % 10) % 10)


def iban_valid(code):
    return int(''.join(str(int(c, 36)) for c in code[4:] + code[:4])) % 97 == 1


def local_name(tag):
    return tag.rsplit('}', 1)[-1]


def xml_bytes(path, raw):
    if path.suffix.lower() != '.p7m':
        return raw
    try:
        out = subprocess.run(
            ['openssl', 'cms', '-verify', '-noverify', '-inform', 'DER', '-binary',
             '-in', str(path), '-out', '-'], capture_output=True, timeout=30)
        if out.returncode == 0 and out.stdout:
            return out.stdout
    except (OSError, subprocess.TimeoutExpired):
        pass
    start = raw.find(b'<?xml')
    return raw[start:raw.rfind(b'>') + 1] if start >= 0 else raw


def sniff_delimiter(lines):
    for delimiter in ';\t|,':
        counts = {line.count(delimiter) for line in lines}
        if len(counts) == 1 and counts != {0}:
            return delimiter
    return ','


def is_header(first, rest):
    if any(classify(cell.strip()) != 'text' for cell in first):
        return False
    return any(classify(row[i].strip()) != 'text'
               for row in rest for i in range(min(len(first), len(row))))
