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


class TestPeerParsing:
    @patch("app.peers.subprocess.run")
    def test_self_ip_extraction_from_wg_show(self, mock_subprocess):
        mock_subprocess.return_value = MagicMock(
            stdout="  address: 10.0.0.2/24\n  peer: some-key\n"
        )
        result = __import__("app.peers", fromlist=["_get_self_ip"])._get_self_ip()
        assert result == "10.0.0.2"

    @patch("app.peers.subprocess.run")
    def test_self_ip_fallback(self, mock_subprocess):
        mock_subprocess.side_effect = Exception("wg not available")
        result = __import__("app.peers", fromlist=["_get_self_ip"])._get_self_ip()
        assert result == "10.0.0.1"


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
