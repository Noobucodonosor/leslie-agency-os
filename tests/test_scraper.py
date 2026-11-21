"""Scraper: estrazione email e persone da HTML statico."""
from scraper import _extract_emails, _extract_people, _is_generic_email
from bs4 import BeautifulSoup


def test_extract_emails_filters_junk():
    text = "Contatti: info@ok.com noreply@ok.com marco.rossi@ok.com abuse@ok.com foo@example.com"
    emails = _extract_emails(text)
    assert 'info@ok.com' in emails
    assert 'marco.rossi@ok.com' in emails
    assert 'noreply@ok.com' not in emails
    assert 'abuse@ok.com' not in emails
    assert not any(e.endswith('example.com') for e in emails)


def test_is_generic_email():
    assert _is_generic_email('info@foo.com') is True
    assert _is_generic_email('CONTACT@foo.com') is True
    assert _is_generic_email('booking@foo.com') is True
    assert _is_generic_email('mario.rossi@foo.com') is False


def test_extract_people_finds_name_role_email():
    html = """
    <html><body>
      <h3>Marco Rossi — CEO</h3>
      <p>Contatti: marco.rossi@studio.example</p>
      <h3>Giulia Bianchi — Sales Director</h3>
      <p>giulia.bianchi@studio.example</p>
      <p>Random caption without a role that should be ignored</p>
    </body></html>
    """
    people = _extract_people(BeautifulSoup(html, 'html.parser'))
    names = {p['name'].lower() for p in people}
    assert 'marco rossi' in names
    assert 'giulia bianchi' in names
    marco = next(p for p in people if p['name'] == 'Marco Rossi')
    assert marco['email'] == 'marco.rossi@studio.example'
    assert marco['is_generic_email'] is False


def test_extract_people_ignores_lines_without_role_keywords():
    html = "<p>Michele Verdi — appassionato di vela</p>"
    people = _extract_people(BeautifulSoup(html, 'html.parser'))
    assert people == []
