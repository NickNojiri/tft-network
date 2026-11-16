"""Runs the live smoke path against a fake Riot server built from synthetic matches."""
import json

from tftnet.demo.synthetic import make_matches
from tftnet.riot.client import RateLimiter, RiotClient
from tftnet import smoke


class FakeResp:
    def __init__(self, payload, status=200):
        self._p, self.status_code, self.headers = payload, status, {}

    def json(self):
        return self._p

    def raise_for_status(self):
        raise RuntimeError(self.status_code)


class FakeSession:
    def __init__(self, matches):
        self.headers, self.matches, self.count_400 = {}, matches, 0

    def get(self, url, params=None, timeout=None):
        if "/league/v1/" in url:
            return FakeResp({"entries": [{"puuid": f"player{i}", "leaguePoints": 100} for i in range(5)]})
        if "/by-puuid/" in url:
            if params["count"] > 20:           # pretend the API rejects big pages
                self.count_400 += 1
                return FakeResp({}, 400)
            return FakeResp(list(self.matches)[:40])
        mid = url.rsplit("/", 1)[1]
        return FakeResp(self.matches[mid]) if mid in self.matches else FakeResp({}, 404)


def test_smoke_end_to_end(settings, tmp_path):
    src = tmp_path / "src"
    make_matches(src, n_matches=60, seed=5)
    matches = {f.stem: json.loads(f.read_text()) for f in src.glob("*.json")}
    sess = FakeSession(matches)
    client = RiotClient("key", limiter=RateLimiter([(10_000, 1.0)]), session=sess)
    assert client.match_ids("p", count=100)  # 400 -> retried with count=20
    assert sess.count_400 == 1
    report = smoke.run(settings, client, n_players=3, n_matches=40)
    assert "Q1 augments field present: False" in report
    assert "H2 ran" in report or "analysis step hit" in report
    assert len(list(settings.matches_dir.glob("*.json"))) == 40
