import json
from datetime import UTC, datetime, timedelta

import httpx

from app.discovery import _pending_candidate
from app.enrichment import PROFILE_FIELDS
from app.hackernews import (
    ALGOLIA_API,
    HN_API,
    SOURCE,
    Comment,
    Thread,
    candidate_fields,
    deobfuscate_emails,
    evidence_rows,
    extract_urls,
    hn_user_id,
    html_to_text,
    parse_comment,
    parse_labelled_lines,
    parse_utc_offset,
    place_only,
    scrape,
    split_technologies,
    store_contact,
    store_thread,
    thread_from_item,
)

THREAD = Thread(
    id=49922568,
    title="Ask HN: Who wants to be hired? (October 2026)",
    posted_at=datetime(2026, 10, 1, 15, 2, tzinfo=UTC),
)

POST_HTML = (
    "Location: Cairo, Egypt (UTC+2)<p>Remote: Yes<p>Willing to relocate: Yes (Germany)"
    "<p>Technologies: AWS, Kubernetes&#x2F;EKS, Python (FastAPI, Flask), CI&#x2F;CD, Terraform"
    "<p>R&#233;sum&#233;&#x2F;CV: "
    '<a href="https:&#x2F;&#x2F;drive.google.com&#x2F;file&#x2F;d&#x2F;'
    '1BTzjxwxDgBfrJWgqAgpeyt1OHZdNU6Xo&#x2F;view" rel="nofollow">'
    "https:&#x2F;&#x2F;drive.google.com&#x2F;file&#x2F;d&#x2F;1BTzjxwxDgBfrJWgqAgpeyt1OHZd...</a>"
    "<p>Email: mohamed.awad9k8@gmail.com"
    '<p>LinkedIn: <a href="https:&#x2F;&#x2F;www.linkedin.com&#x2F;in&#x2F;mohamedawad9k8&#x2F;"'
    ' rel="nofollow">https:&#x2F;&#x2F;www.linkedin.com&#x2F;in&#x2F;mohamedawad9k8&#x2F;</a>'
    '<p>GitHub: <a href="https:&#x2F;&#x2F;github.com&#x2F;MohamedAwad9k8" rel="nofollow">'
    "https:&#x2F;&#x2F;github.com&#x2F;MohamedAwad9k8</a>"
    "<p>I&#x27;m an AWS Cloud Engineer. Note: open to contract work."
)


def _comment(text_html: str = POST_HTML, *, author: str = "mohamedawad9k8", id: int = 50103867):
    return Comment(
        id=id,
        author=author,
        thread_id=THREAD.id,
        text_html=text_html,
        posted_at=datetime(2026, 10, 15, 14, 4, 9, tzinfo=UTC),
    )


def test_html_to_text_turns_paragraphs_into_lines() -> None:
    text = html_to_text("Location: Berlin<p>Remote: Yes<br>Email: a@b.co<p>I&#x27;m here")
    assert text == "Location: Berlin\nRemote: Yes\nEmail: a@b.co\nI'm here"


def test_labelled_lines_take_the_first_known_label_only() -> None:
    fields = parse_labelled_lines(
        "Role: Backend\nLocation: Lisbon\nlocation: ignored\nNote: not a field\nRésumé/CV: x"
    )
    assert fields == {"role": "Backend", "location": "Lisbon", "resume": "x"}


def test_obfuscated_emails_are_read() -> None:
    assert deobfuscate_emails("write to ada [at] example [dot] com") == ["ada@example.com"]
    assert deobfuscate_emails("ada (at) mail (dot) example (dot) org") == ["ada@mail.example.org"]
    assert deobfuscate_emails("ada at example dot co dot uk") == ["ada@example.co.uk"]
    assert deobfuscate_emails("meet me at noon") == []


def test_truncated_link_text_is_replaced_by_the_href() -> None:
    raw = (
        'CV: <a href="https://example.com/a/very/long/path/that/hn/cuts">'
        "https://example.com/a/very/long/path/that...</a>"
    )
    urls = extract_urls(html_to_text(raw), raw)
    assert urls == ["https://example.com/a/very/long/path/that/hn/cuts"]


def test_technologies_split_outside_brackets() -> None:
    assert split_technologies("Python (FastAPI, Flask), CI/CD; Go | Rust, , rust") == [
        "Python (FastAPI, Flask)",
        "CI/CD",
        "Go",
        "Rust",
    ]


def test_utc_offset_and_place() -> None:
    assert parse_utc_offset("Bangalore, India (UTC+5:30)") == "+05:30"
    assert parse_utc_offset("Denver (GMT-7)") == "-07:00"
    assert parse_utc_offset("Berlin") is None
    assert place_only("Bangalore, India (UTC+5:30)") == "Bangalore, India"
    assert place_only("Remote (US)") == "Remote (US)"


def test_hn_user_id_is_negative_and_stable() -> None:
    assert hn_user_id("pg") == hn_user_id(" PG ")
    assert hn_user_id("pg") < 0
    assert hn_user_id("pg") != hn_user_id("pg2")
    assert abs(hn_user_id("pg")) < 2**53


def test_parse_comment_reads_every_published_detail() -> None:
    contact = parse_comment(_comment())
    assert contact.login == "mohamedawad9k8"
    assert contact.source_url == "https://news.ycombinator.com/item?id=50103867"
    assert contact.location_raw == "Cairo, Egypt (UTC+2)"
    assert contact.utc_offset == "+02:00"
    assert contact.remote == "Yes"
    assert contact.relocate == "Yes (Germany)"
    assert contact.technologies == [
        "AWS",
        "Kubernetes/EKS",
        "Python (FastAPI, Flask)",
        "CI/CD",
        "Terraform",
    ]
    assert contact.resume_url == (
        "https://drive.google.com/file/d/1BTzjxwxDgBfrJWgqAgpeyt1OHZdNU6Xo/view"
    )
    assert contact.emails == ["mohamed.awad9k8@gmail.com"]
    assert contact.github_login == "MohamedAwad9k8"
    assert contact.github_url == "https://github.com/MohamedAwad9k8"
    assert contact.linkedin_url == "https://www.linkedin.com/in/mohamedawad9k8"
    kinds = {kind for kind, _ in contact.urls}
    assert kinds == {"linkedin", "website"}


def test_mailto_and_placeholder_emails() -> None:
    raw = (
        'Email: <a href="mailto:ada@example.org">mail me</a> '
        "or your.email@example.com or ada@users.noreply.github.com"
    )
    contact = parse_comment(_comment(raw))
    assert contact.emails == ["ada@example.org"]


def test_candidate_fields_match_the_github_pipeline() -> None:
    now = datetime(2026, 11, 6, tzinfo=UTC)
    document = candidate_fields(parse_comment(_comment()), THREAD, now)
    github_shape = _pending_candidate("job", {"id": 1, "login": "x"}, "https://github.com/x", now)
    assert set(github_shape) <= set(document)
    assert set(PROFILE_FIELDS) <= set(document)
    assert document["source"] == SOURCE
    assert document["login"] == "mohamedawad9k8"
    assert document["profile_url"] == "https://news.ycombinator.com/user?id=mohamedawad9k8"
    assert document["github_user_id"] == hn_user_id("mohamedawad9k8")
    assert document["public_email"] == "mohamed.awad9k8@gmail.com"
    assert document["country"] == "Egypt"
    assert document["utc_offset"] == "+02:00"
    assert document["enrichment_status"] == "enriched"
    assert document["has_email"] is True
    assert document["personal"] is True
    assert document["other"] is False
    assert document["has_linkedin"] is True
    assert document["linkedin_url"] == "https://www.linkedin.com/in/mohamedawad9k8"
    assert document["hn_thread_id"] == THREAD.id
    assert document["hn_comment_id"] == 50103867
    assert document["github_login"] == "MohamedAwad9k8"
    assert document["last_checked_at"] == now
    assert document["source"] == SOURCE
    assert document["sources"] == [SOURCE]
    assert document["hn_username"] == "mohamedawad9k8"


def test_evidence_rows_use_the_evidence_index_keys() -> None:
    rows = evidence_rows(parse_comment(_comment()))
    kinds = sorted(row["kind"] for row in rows)
    assert kinds == ["email", "linkedin", "website"]
    for row in rows:
        assert row["github_user_id"] == hn_user_id("mohamedawad9k8")
        assert row["source_url"] == "https://news.ycombinator.com/item?id=50103867"
        assert row["normalized_value"]


def test_thread_filter_keeps_only_wants_to_be_hired() -> None:
    assert thread_from_item({"id": 1, "title": "Ask HN: Who is hiring? (November 2026)"}) is None
    assert thread_from_item({"id": 2, "title": "Ask HN: Freelancer? Seeking freelancer?"}) is None
    thread = thread_from_item(
        {"id": 3, "title": "Ask HN: Who wants to be hired? (November 2026)", "time": 1793635297}
    )
    assert thread is not None
    assert thread.url == "https://news.ycombinator.com/item?id=3"
    assert thread.posted_at.year == 2026


NOW = datetime(2026, 11, 6, 12, 0, tzinfo=UTC)
OLD = NOW - timedelta(days=20)


def _github_candidate(login: str, user_id: int, **extra) -> dict:
    document = {
        "job_id": "job-1",
        "github_user_id": user_id,
        "login": login,
        "profile_url": f"https://github.com/{login}",
        "name": None,
        "public_email": None,
        "last_checked_at": OLD,
    }
    document.update(extra)
    return document


async def _emails(db, user_id: int) -> list[str]:
    values = await db.contact_evidence.distinct(
        "normalized_value", {"github_user_id": user_id, "kind": "email"}
    )
    return sorted(values)


async def test_store_inserts_a_new_person(db) -> None:
    stored = await store_contact(db, parse_comment(_comment()), THREAD, now=NOW, job_id="hn-1")
    assert stored.outcome == "inserted"
    document = await db.candidates.find_one({"login": "mohamedawad9k8"})
    assert document["source"] == SOURCE
    assert document["sources"] == [SOURCE]
    assert document["hn_username"] == "mohamedawad9k8"
    assert document["job_id"] == "hn-1"
    assert document["public_email"] == "mohamed.awad9k8@gmail.com"
    assert document["created_at"] == NOW
    user_id = document["github_user_id"]
    assert await db.contact_evidence.count_documents({"github_user_id": user_id}) == 3


async def test_returning_user_is_fresh_then_has_email(db) -> None:
    first = parse_comment(_comment())
    await store_contact(db, first, THREAD, now=OLD)
    await db.candidates.update_one(
        {"login": "mohamedawad9k8"}, {"$set": {"outreach_status": "message_sent"}}
    )

    # The same post read again a day later: nothing new, nothing written.
    again = await store_contact(db, first, THREAD, now=OLD + timedelta(days=1))
    assert again.outcome == "fresh"

    # Three weeks on, a new post with a work address: the personal primary stays.
    later = parse_comment(_comment("Email: new@example.org", id=50103868))
    stored = await store_contact(db, later, THREAD, now=NOW)
    assert stored.outcome == "has_email"
    document = await db.candidates.find_one({"login": "mohamedawad9k8"})
    assert document["public_email"] == "mohamed.awad9k8@gmail.com"
    assert document["hn_comment_id"] == 50103867
    assert document["outreach_status"] == "message_sent"
    assert await _emails(db, document["github_user_id"]) == ["mohamed.awad9k8@gmail.com"]


async def test_returning_user_without_email_is_updated_and_moved(db) -> None:
    quiet = parse_comment(
        _comment(
            'Location: Lisbon<p>LinkedIn: <a href="https://www.linkedin.com/in/ada">x</a>',
            author="ada",
            id=100,
        )
    )
    await store_contact(db, quiet, THREAD, now=OLD, job_id="hn-1")
    await db.candidates.update_one({"login": "ada"}, {"$set": {"outreach_status": "no_reply"}})

    louder = parse_comment(_comment("Email: ada@example.org", author="ada", id=200))
    stored = await store_contact(db, louder, THREAD, now=NOW, job_id="hn-2")
    assert stored.outcome == "updated"
    assert stored.previous_job_id == "hn-1"
    assert stored.moved is not None and stored.moved["discovered"] == 1
    document = await db.candidates.find_one({"login": "ada"})
    assert document["job_id"] == "hn-2"
    assert document["public_email"] == "ada@example.org"
    assert document["hn_comment_id"] == 200
    assert document["has_email"] is True
    assert document["has_linkedin"] is True
    assert "outreach_status" not in document


async def test_personal_email_merges_into_linked_github_record_and_clears_status(db) -> None:
    await db.candidates.insert_one(
        _github_candidate(
            "MohamedAwad9k8",
            42,
            public_email="work@example.org",
            outreach_status="message_sent",
            last_checked_at=NOW,
            country="Egypt",
        )
    )
    await db.contact_evidence.insert_one(
        {
            "github_user_id": 42,
            "kind": "email",
            "normalized_value": "work@example.org",
            "raw_value": "work@example.org",
            "source_url": "https://github.com/MohamedAwad9k8",
            "validation_status": "valid",
            "created_at": OLD,
            "last_seen_at": OLD,
        }
    )
    stored = await store_contact(db, parse_comment(_comment()), THREAD, now=NOW, job_id="hn-1")
    assert stored.outcome == "merged"
    assert await db.candidates.count_documents({}) == 1
    document = await db.candidates.find_one({"github_user_id": 42})
    assert document["login"] == "MohamedAwad9k8"
    assert document["job_id"] == "job-1"
    assert document["public_email"] == "mohamed.awad9k8@gmail.com"
    assert "outreach_status" not in document
    assert document["sources"] == [SOURCE]
    assert document["hn_username"] == "mohamedawad9k8"
    assert document["hn_comment_id"] == 50103867
    assert document["location_raw"] == "Cairo, Egypt (UTC+2)"
    assert document["personal"] is True and document["other"] is True
    assert document["has_linkedin"] is True
    assert await _emails(db, 42) == ["mohamed.awad9k8@gmail.com", "work@example.org"]


async def test_work_email_does_not_replace_a_personal_primary(db) -> None:
    await db.candidates.insert_one(
        _github_candidate("MohamedAwad9k8", 42, public_email="ada@gmail.com", outreach_status="x")
    )
    post = parse_comment(
        _comment(
            "Email: ada@work.example.org<p>"
            'GitHub: <a href="https://github.com/MohamedAwad9k8">gh</a>'
        )
    )
    stored = await store_contact(db, post, THREAD, now=NOW)
    assert stored.outcome == "has_email"
    document = await db.candidates.find_one({"github_user_id": 42})
    assert document["public_email"] == "ada@gmail.com"
    assert document["outreach_status"] == "x"
    assert "hn_username" not in document


async def test_email_match_merges_into_a_github_record_without_a_link(db) -> None:
    await db.candidates.insert_one(_github_candidate("grace", 8))
    await db.contact_evidence.insert_one(
        {
            "github_user_id": 8,
            "kind": "email",
            "normalized_value": "grace@example.org",
            "raw_value": "grace@example.org",
            "source_url": "https://github.com/grace/repo",
            "validation_status": "valid",
            "created_at": OLD,
            "last_seen_at": OLD,
        }
    )
    post = parse_comment(
        _comment(
            'Email: grace@example.org<p><a href="https://www.linkedin.com/in/grace">in</a>',
            author="gh0st",
            id=300,
        )
    )
    stored = await store_contact(db, post, THREAD, now=NOW)
    assert stored.outcome == "merged"
    document = await db.candidates.find_one({"github_user_id": 8})
    assert document["login"] == "grace"
    assert document["public_email"] == "grace@example.org"
    assert document["linkedin_url"] == "https://www.linkedin.com/in/grace"
    assert await db.candidates.count_documents({}) == 1


async def test_email_match_joins_two_hn_accounts(db) -> None:
    first = parse_comment(_comment("Email: ada@work.example.org", author="ada", id=1))
    await store_contact(db, first, THREAD, now=OLD)
    second = parse_comment(
        _comment("Email: ada@work.example.org, ada@gmail.com", author="ada2", id=2)
    )
    stored = await store_contact(db, second, THREAD, now=NOW)
    assert stored.outcome == "updated"
    assert await db.candidates.count_documents({}) == 1
    document = await db.candidates.find_one({"login": "ada"})
    assert document["public_email"] == "ada@gmail.com"
    assert await _emails(db, document["github_user_id"]) == [
        "ada@gmail.com",
        "ada@work.example.org",
    ]


async def test_unrelated_github_login_collision_gets_a_prefix(db) -> None:
    await db.candidates.insert_one(_github_candidate("mohamedawad9k8", 42))
    post = parse_comment(_comment("Email: other@example.org"))
    stored = await store_contact(db, post, THREAD, now=NOW)
    assert stored.outcome == "inserted"
    document = await db.candidates.find_one({"login": "hn:mohamedawad9k8"})
    assert document["hn_username"] == "mohamedawad9k8"
    assert document["profile_url"] == "https://news.ycombinator.com/user?id=mohamedawad9k8"
    github = await db.candidates.find_one({"github_user_id": 42})
    assert github["public_email"] is None


async def test_store_thread_counts_each_outcome(db) -> None:
    comments = [
        _comment(),
        _comment("Location: Mars<p>Remote: Yes<p>No way to reach me.", author="quiet", id=2),
    ]
    result = await store_thread(db, THREAD, comments, now=OLD)
    assert (result.inserted, result.updated, result.skipped) == (1, 0, 1)
    result = await store_thread(db, THREAD, comments, now=OLD + timedelta(days=1))
    assert (result.inserted, result.fresh, result.skipped) == (0, 1, 1)
    assert await db.candidates.count_documents({"source": SOURCE}) == 1


def _mock_transport() -> httpx.MockTransport:
    hiring = {
        "id": 50338728,
        "title": "Ask HN: Who is hiring? (November 2026)",
        "time": 1793635297,
        "type": "story",
    }
    wanted = {
        "id": 50338726,
        "title": "Ask HN: Who wants to be hired? (November 2026)",
        "time": 1793635297,
        "type": "story",
        "kids": [1, 2],
    }
    hits = [
        {
            "objectID": "1",
            "author": "ada",
            "parent_id": 50338726,
            "story_id": 50338726,
            "created_at_i": 1793640000,
            "comment_text": "Location: Lisbon, Portugal<p>Email: ada@example.org",
        },
        {
            "objectID": "2",
            "author": "reply-guy",
            "parent_id": 1,
            "story_id": 50338726,
            "created_at_i": 1793640001,
            "comment_text": "Nice post, mail me at reply@example.org",
        },
    ]

    def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == f"{HN_API}/user/whoishiring.json":
            return httpx.Response(
                200, json={"id": "whoishiring", "submitted": [50338728, 50338726]}
            )
        if url == f"{HN_API}/item/50338728.json":
            return httpx.Response(200, json=hiring)
        if url == f"{HN_API}/item/50338726.json":
            return httpx.Response(200, json=wanted)
        if url.startswith(f"{ALGOLIA_API}/search_by_date"):
            assert request.url.params["tags"] == "comment,story_50338726"
            return httpx.Response(200, json={"hits": hits, "nbPages": 1, "nbHits": 2})
        return httpx.Response(404, json=None)

    return httpx.MockTransport(handle)


async def test_scrape_reads_only_developer_posts_of_the_wanted_thread(db) -> None:
    async with httpx.AsyncClient(transport=_mock_transport()) as client:
        results = await scrape(db, threads=1, client=client)
    assert len(results) == 1
    thread, result = results[0]
    assert thread.id == 50338726
    assert (result.inserted, result.updated, result.skipped) == (1, 0, 0)
    stored = await db.candidates.find_one({"login": "ada"})
    assert stored["public_email"] == "ada@example.org"
    assert stored["country"] == "Portugal"
    assert await db.candidates.find_one({"login": "reply-guy"}) is None
    assert json.loads(json.dumps(stored["technologies"])) == []


async def test_scrape_falls_back_to_the_item_api(db) -> None:
    wanted = {
        "id": 50338726,
        "title": "Ask HN: Who wants to be hired? (November 2026)",
        "time": 1793635297,
        "type": "story",
        "kids": [7, 8],
    }

    def handle(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == f"{HN_API}/user/whoishiring.json":
            return httpx.Response(200, json={"submitted": [50338726]})
        if url == f"{HN_API}/item/50338726.json":
            return httpx.Response(200, json=wanted)
        if url == f"{HN_API}/item/7.json":
            return httpx.Response(
                200,
                json={
                    "id": 7,
                    "type": "comment",
                    "by": "grace",
                    "time": 1793640000,
                    "text": "Email: grace [at] example [dot] com",
                },
            )
        if url == f"{HN_API}/item/8.json":
            return httpx.Response(200, json={"id": 8, "type": "comment", "deleted": True})
        if url.startswith(ALGOLIA_API):
            return httpx.Response(503, text="down")
        return httpx.Response(404, json=None)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        results = await scrape(db, threads=1, client=client)
    assert results[0][1].inserted == 1
    stored = await db.candidates.find_one({"login": "grace"})
    assert stored["public_email"] == "grace@example.com"
