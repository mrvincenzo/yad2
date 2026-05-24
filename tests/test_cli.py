import json
import pytest
from pathlib import Path
from click.testing import CliRunner
from yad2_watcher.cli import cli


@pytest.fixture
def mock_fetch_item_data(mocker):
    return mocker.patch("yad2_watcher.cli.fetch_item_data")


@pytest.fixture
def mock_requests_get(mocker):
    class MockResponse:
        def __init__(self):
            self.content = b"fake_image_data"
            
        def raise_for_status(self):
            pass
            
        def iter_content(self, chunk_size):
            yield self.content
            
    mock_get = mocker.patch("yad2_watcher.cli.requests.get")
    mock_get.return_value = MockResponse()
    return mock_get


def test_download_apartment_success(mock_fetch_item_data, mock_requests_get, tmp_path, mocker):
    # Mock the Path object inside the cli to write to our tmp_path
    class MockPath:
        def __new__(cls, *args, **kwargs):
            return tmp_path.joinpath(*args)

    mocker.patch("yad2_watcher.cli.Path", MockPath)

    # Setup mock data
    mock_data = {
        "price": "5000",
        "address": {
            "street": {"text": "Main St"},
            "house": {"number": "10"},
            "neighborhood": {"text": "Downtown"},
            "city": {"text": "Metropolis"}
        },
        "metaData": {
            "description": "A nice apartment.",
            "images": [
                "http://example.com/img1.jpeg",
                "http://example.com/img2.png"
            ]
        }
    }
    mock_fetch_item_data.return_value = mock_data

    runner = CliRunner()
    result = runner.invoke(cli, ["download", "https://yad2.co.il/item/faketoken"])

    assert result.exit_code == 0
    assert "Fetching data for apartment faketoken" in result.output
    assert "Downloading 2 photo(s)" in result.output

    # Check if files were created
    out_dir = tmp_path / "downloads" / "faketoken"
    assert out_dir.exists()
    assert (out_dir / "details.json").exists()
    assert (out_dir / "summary.md").exists()
    assert (out_dir / "photo_01.jpeg").exists()
    assert (out_dir / "photo_02.png").exists()

    # Check summary content
    summary_content = (out_dir / "summary.md").read_text(encoding="utf-8")
    assert "# Apartment Details: faketoken" in summary_content
    assert "**Price:** 5000 ₪" in summary_content
    assert "Main St, 10, Downtown, Metropolis" in summary_content
    assert "A nice apartment." in summary_content

    # Check json content
    json_content = json.loads((out_dir / "details.json").read_text(encoding="utf-8"))
    assert json_content["price"] == "5000"


def test_download_apartment_invalid_token():
    runner = CliRunner()
    result = runner.invoke(cli, ["download", "https://yad2.co.il/"])
    
    # Token extraction should fail or return empty, which errors out
    assert result.exit_code == 1
    assert "Could not extract a valid token" in result.output


def test_download_apartment_fetch_error(mock_fetch_item_data):
    mock_fetch_item_data.side_effect = Exception("Network error")
    
    runner = CliRunner()
    result = runner.invoke(cli, ["download", "faketoken"])
    
    assert result.exit_code == 1
    assert "Failed to fetch data: Network error" in result.output
