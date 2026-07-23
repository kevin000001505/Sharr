import pytest
from unittest.mock import patch, MagicMock

from app.schemas import ProgressEvent, TransferJob, TransferRequest, Peer
from app.transfer import new_job, build_rsync_command, parse_rsync_progress
from app.peers import is_valid_peer


class TestSchemas:
    def test_peer_model(self):
        p = Peer(name="alice", tunnel_ip="10.0.0.2", online=True)
        assert p.name == "alice"
        assert p.model_dump() == {
            "name": "alice",
            "tunnel_ip": "10.0.0.2",
            "online": True,
        }

    def test_transfer_job_model(self):
        j = TransferJob(
            job_id="abc",
            target_peer_ip="10.0.0.2",
            source_path="/data/file.txt",
            dest_path="/data/file.txt",
            status="running",
            progress=50.0,
            bytes_sent=1024,
            total_bytes=2048,
            speed="1MB/s",
            created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
        )
        assert j.job_id == "abc"
        assert j.progress == 50.0
        assert j.total_bytes == 2048

    def test_progress_event_model(self):
        e = ProgressEvent(
            job_id="abc",
            progress=25.0,
            bytes_sent=512,
            speed="5MB/s",
            status="running",
        )
        assert e.progress == 25.0


class TestNewJob:
    def test_new_job_creates_with_correct_fields(self):
        req = TransferRequest(
            target_peer_ip="10.0.0.2",
            source_path="/data/file.txt",
            dest_path="/data/file.txt",
        )
        job = new_job(req)
        assert job.target_peer_ip == "10.0.0.2"
        assert job.source_path == "/data/file.txt"
        assert job.dest_path == "/data/file.txt"
        assert job.status == "queued"
        assert job.progress == 0
        assert job.bytes_sent == 0

    def test_new_job_enforces_path_confinement(self):
        req = TransferRequest(
            target_peer_ip="10.0.0.2",
            source_path="/etc/shadow",
            dest_path="/data/file.txt",
        )
        with patch("app.transfer.settings") as mock_settings:
            mock_settings.allowed_base_dir = "/data"
            pytest.raises(ValueError, new_job, req)


class TestBuildRsyncCommand:
    def test_command_is_list(self):
        job = TransferJob(
            job_id="abc",
            target_peer_ip="10.0.0.2",
            source_path="/data/file.txt",
            dest_path="/data/file.txt",
            status="running",
            created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
        )
        cmd = build_rsync_command(job)
        assert isinstance(cmd, list)
        assert cmd[0] == "rsync"
        assert "-a" in cmd
        assert "--info=progress2" in cmd

    def test_command_contains_ssh_hardening(self):
        job = TransferJob(
            job_id="abc",
            target_peer_ip="10.0.0.3",
            source_path="/data/file.txt",
            dest_path="/data/file.txt",
            status="running",
            created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
        )
        cmd = build_rsync_command(job)
        ssh_part = " ".join(cmd)
        assert "StrictHostKeyChecking=no" in ssh_part
        assert "UserKnownHostsFile=/dev/null" in ssh_part
        assert "BatchMode=yes" in ssh_part

    def test_command_contains_target_and_path(self):
        job = TransferJob(
            job_id="abc",
            target_peer_ip="10.0.0.3",
            source_path="/data/video.mp4",
            dest_path="/backup/video.mp4",
            status="running",
            created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
        )
        cmd = build_rsync_command(job)
        cmd_str = " ".join(cmd)
        assert "10.0.0.3" in cmd_str
        assert "/backup/video.mp4" in cmd_str


class TestParseRsyncProgress:
    def test_parse_real_progress_line(self):
        line = "1,234,567 45% 11.22MB/s 0:00:03"
        result = parse_rsync_progress(line)
        assert result is not None
        assert result.progress == 45.0
        assert result.bytes_sent == 1234567
        assert result.speed == "11.22MB/s"

    def test_parse_progress_without_time(self):
        line = "512 10% 5MB/s"
        result = parse_rsync_progress(line)
        assert result is not None
        assert result.progress == 10.0
        assert result.bytes_sent == 512

    def test_parse_comma_separated_bytes(self):
        line = "1,048,576 50% 2MB/s 0:00:01"
        result = parse_rsync_progress(line)
        assert result is not None
        assert result.bytes_sent == 1048576

    def test_non_progress_line(self):
        line = "sending incremental file list"
        result = parse_rsync_progress(line)
        assert result is None

    def test_empty_line(self):
        result = parse_rsync_progress("")
        assert result is None


class TestIsValidPeer:
    @patch("app.peers.parse_wg_peers")
    def test_valid_peer(self, mock_peers):
        mock_peers.return_value = [
            Peer(name="alice", tunnel_ip="10.0.0.2", online=True),
            Peer(name="bob", tunnel_ip="10.0.0.3", online=False),
        ]
        assert is_valid_peer("10.0.0.2") is True
        assert is_valid_peer("10.0.0.3") is True
        assert is_valid_peer("10.0.0.99") is False

    @patch("app.peers.parse_wg_peers")
    def test_no_peers(self, mock_peers):
        mock_peers.return_value = []
        assert is_valid_peer("10.0.0.2") is False


WG_CONF = """\
[Interface]
Address = 10.0.0.2/24
PrivateKey = privkey
ListenPort = 51820

# Friend A
[Peer]
# alice
PublicKey = keyA
AllowedIPs = 10.0.0.1/32
Endpoint = 203.0.113.1:51820

# Friend C
[Peer]
# carol
PublicKey = keyC
AllowedIPs = 10.0.0.3/32
Endpoint = 203.0.113.3:51820
"""


class TestPeerParsing:
    def test_self_ip_extraction_from_interface(self, tmp_path):
        import app.peers as peers_mod
        conf = tmp_path / "wg0.conf"
        conf.write_text(WG_CONF)
        with patch.object(peers_mod.settings, "wg_conf_path", str(conf)):
            assert peers_mod._get_self_ip() == "10.0.0.2"

    def test_self_ip_fallback(self, tmp_path):
        import app.peers as peers_mod
        with patch.object(peers_mod.settings, "wg_conf_path",
                          str(tmp_path / "missing.conf")):
            assert peers_mod._get_self_ip() == "10.0.0.1"

    def test_peer_names_not_shifted(self, tmp_path):
        """The trailing '# Friend C' comment must not mislabel the first peer."""
        import app.peers as peers_mod
        conf = tmp_path / "wg0.conf"
        conf.write_text(WG_CONF)
        with patch.object(peers_mod.settings, "wg_conf_path", str(conf)):
            peers = peers_mod.parse_wg_peers()
        by_ip = {p.tunnel_ip: p.name for p in peers}
        # Self (10.0.0.2) is excluded; the other two keep their own names.
        assert by_ip == {"10.0.0.1": "alice", "10.0.0.3": "carol"}


class TestHTTPRoutes:
    @pytest.fixture
    def client(self):
        from fastapi.testclient import TestClient
        from app.main import create_app
        app = create_app()
        return TestClient(app)

    def test_peers_endpoint(self, client):
        with patch("app.routes.peers.list_peers") as mock_list:
            mock_list.return_value = [
                Peer(name="alice", tunnel_ip="10.0.0.2", online=True),
            ]
            resp = client.get("/api/peers")
            assert resp.status_code == 200
            assert len(resp.json()) == 1

    def test_create_transfer_invalid_peer_returns_400(self, client):
        resp = client.post("/api/transfers", json={
            "target_peer_ip": "10.0.0.99",
            "source_path": "/data/file.txt",
            "dest_path": "/data/file.txt",
        })
        assert resp.status_code == 400

    def test_create_transfer_valid(self, client):
        with patch("app.routes.transfers.is_valid_peer", return_value=True):
            with patch("app.routes.transfers.save_job", new=MagicMock()):
                with patch("app.routes.transfers.publish_job", new=MagicMock()):
                    resp = client.post("/api/transfers", json={
                        "target_peer_ip": "10.0.0.2",
                        "source_path": "/data/file.txt",
                        "dest_path": "/data/file.txt",
                    })
                    assert resp.status_code == 200
                    assert "job_id" in resp.json()

    def test_list_transfers(self, client):
        with patch("app.routes.transfers.list_jobs", new=MagicMock()) as mock_list:
            mock_list.return_value = []
            resp = client.get("/api/transfers")
            assert resp.status_code == 200

    def test_get_transfer_not_found(self, client):
        with patch("app.routes.transfers.load_job", new=MagicMock()) as mock_load:
            mock_load.return_value = None
            resp = client.get("/api/transfers/nonexistent")
            assert resp.status_code == 404

    def test_cancel_transfer(self, client):
        with patch("app.routes.transfers.request_cancel", new=MagicMock()) as mock_cancel:
            resp = client.post("/api/transfers/abc123/cancel")
            assert resp.status_code == 200
            mock_cancel.assert_called_once_with("abc123")


def _make_media_tree(tmp_path):
    """Build the expected on-disk layout: movie/<title>/, tv_show/tv/<show>/seasonN/."""
    movies = tmp_path / "movie"
    tv = tmp_path / "tv_show" / "tv"
    m = movies / "The Matrix (1999)"
    m.mkdir(parents=True)
    (m / "The Matrix (1999).mkv").write_bytes(b"x" * 100)
    empty = movies / "Empty Folder"
    empty.mkdir()
    show = tv / "Breaking Bad (2008)"
    s1 = show / "season1"
    s1.mkdir(parents=True)
    (s1 / "S01E01 Pilot.mkv").write_bytes(b"x" * 10)
    (s1 / "S01E02 Cat.mkv").write_bytes(b"x" * 10)
    s2 = show / "Season 2"
    s2.mkdir()
    (s2 / "S02E01.mp4").write_bytes(b"x" * 10)
    return movies, tv


@pytest.fixture
def media_settings(tmp_path):
    import app.library as library_mod
    movies, tv = _make_media_tree(tmp_path)
    with patch.object(library_mod.settings, "movies_dir", str(movies)), \
         patch.object(library_mod.settings, "tv_dir", str(tv)), \
         patch.object(library_mod, "_tmdb_lookup",
                      return_value={"poster": "http://img/p.jpg",
                                    "overview": "plot", "year": None}):
        yield library_mod


class TestLibraryScanner:
    def test_list_movies(self, media_settings):
        movies = media_settings.list_movies()
        assert len(movies) == 1  # empty folder skipped
        m = movies[0]
        assert m["id"] == "The Matrix (1999)"
        assert m["title"] == "The Matrix"
        assert m["year"] == 1999
        assert m["poster"] == "http://img/p.jpg"
        assert m["size"] == 100

    def test_list_shows(self, media_settings):
        shows = media_settings.list_shows()
        assert len(shows) == 1
        s = shows[0]
        assert s["title"] == "Breaking Bad"
        assert s["episode_count"] == 3
        assert s["season_count"] == 2

    def test_show_detail_seasons_and_episodes(self, media_settings):
        d = media_settings.show_detail("Breaking Bad (2008)")
        assert [s["season"] for s in d["seasons"]] == [1, 2]
        eps = d["seasons"][0]["episodes"]
        assert [e["episode"] for e in eps] == [1, 2]
        assert eps[0]["episode_path"] == "Breaking Bad (2008)/season1/S01E01 Pilot.mkv"

    def test_resolve_movie(self, media_settings):
        src, rel = media_settings.resolve_request("movie", "The Matrix (1999)")
        assert src.endswith("/The Matrix (1999)")
        assert rel == ""

    def test_resolve_season_and_episode(self, media_settings):
        src, rel = media_settings.resolve_request(
            "season", "Breaking Bad (2008)", season=2)
        assert src.endswith("/Season 2")
        assert rel == "Breaking Bad (2008)"
        src, rel = media_settings.resolve_request(
            "episode", "Breaking Bad (2008)",
            episode_path="Breaking Bad (2008)/season1/S01E01 Pilot.mkv")
        assert src.endswith("S01E01 Pilot.mkv")
        assert rel == "Breaking Bad (2008)/season1"

    def test_resolve_rejects_traversal(self, media_settings):
        with pytest.raises(media_settings.LibraryError):
            media_settings.resolve_request("movie", "../../etc")
        with pytest.raises(media_settings.LibraryError):
            media_settings.resolve_request(
                "episode", "x", episode_path="../../../etc/passwd")

    def test_resolve_unknown_title(self, media_settings):
        with pytest.raises(media_settings.LibraryError):
            media_settings.resolve_request("movie", "Nope (2000)")


class TestTmdbLookup:
    def test_parses_search_response(self):
        import app.library as library_mod
        fake_resp = MagicMock()
        fake_resp.json.return_value = {"results": [{
            "poster_path": "/abc.jpg",
            "overview": "A hacker discovers reality.",
            "release_date": "1999-03-31",
        }]}
        fake_redis = MagicMock()
        fake_redis.get.return_value = None
        with patch.object(library_mod.settings, "tmdb_api_key", "k"), \
             patch.object(library_mod, "get_redis", return_value=fake_redis), \
             patch.object(library_mod.httpx, "get", return_value=fake_resp) as g:
            meta = library_mod._tmdb_lookup("The Matrix", 1999, "movie")
        assert meta == {"poster": "https://image.tmdb.org/t/p/w342/abc.jpg",
                        "overview": "A hacker discovers reality.", "year": 1999}
        assert g.call_args.kwargs["params"]["year"] == 1999
        fake_redis.set.assert_called_once()

    def test_no_key_returns_empty(self):
        import app.library as library_mod
        with patch.object(library_mod.settings, "tmdb_api_key", ""):
            assert library_mod._tmdb_lookup("X", None, "movie") == {}


class TestWorkerReRaise:
    def test_handle_job_re_raises(self):
        from app.worker import handle_job
        from app.schemas import TransferJob

        job = TransferJob(
            job_id="fail",
            target_peer_ip="10.0.0.2",
            source_path="/data/x",
            dest_path="/data/x",
            status="queued",
            created_at="2024-01-01T00:00:00+00:00",
            updated_at="2024-01-01T00:00:00+00:00",
        )

        with patch("app.worker.run_transfer") as mock_run:
            mock_run.side_effect = RuntimeError("test error")
            with patch("app.worker.save_job"), \
                 patch("app.worker.publish_progress"):
                with pytest.raises(RuntimeError):
                    handle_job(job)
