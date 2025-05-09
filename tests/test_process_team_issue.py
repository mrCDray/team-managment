import os
import sys
import json
from unittest.mock import patch, MagicMock
import pytest
import requests  # Add explicit import for requests

# Add the scripts directory to the path so we can import the module
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Create a mock for sync_github_teams before importing process_team_issue
# The key issue: need to mock at the correct location where it's being imported
sync_github_teams_mock = MagicMock()
sys.modules["scripts.sync_github_teams"] = sync_github_teams_mock
sys.modules["sync_github_teams"] = sync_github_teams_mock  # Also mock the direct import path

# Now import the modules
import scripts.process_team_issue as team_module

# Set the sync_teams attribute in the mock
sync_github_teams_mock.sync_teams = MagicMock()


@pytest.fixture
def sample_issue_body():
    """Sample issue body for testing."""
    return """
### Action
create

### Team Name
test-team

### Project Name
Test Project

### Team Description
This is a test team description

### Child Teams
- developers:Team responsible for development:write
- testers:Team for QA:read

### Members
- @test-user (developers, testers)
- @admin-user (all)

### Repositories
- repo1
- repo2
"""


@pytest.fixture
def mock_env_vars(monkeypatch):
    """Setup environment variables for tests."""
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.setenv("GITHUB_ORG", "test-org")
    monkeypatch.setenv("REPO", "test-org/test-repo")
    monkeypatch.setenv("ISSUE_NUMBER", "1")
    monkeypatch.setenv(
        "ISSUE_BODY",
        json.dumps(
            """
### Action
create

### Team Name
test-team

### Project Name
Test Project

### Team Description
This is a test team description

### Child Teams
- developers:Team responsible for development:write
- testers:Team for QA:read

### Members
- @test-user (developers, testers)
- @admin-user (all)

### Repositories
- repo1
- repo2
"""
        ),
    )


@patch("scripts.process_team_issue.requests.get")
def test_check_user_in_org(mock_get):
    """Test checking if user exists in organization."""
    # Setup environment variable for all tests
    with patch.dict(os.environ, {"GITHUB_ORG": "test-org"}):
        # User exists
        mock_response = MagicMock()
        mock_response.status_code = 204
        mock_get.return_value = mock_response

        result = team_module.check_user_in_org("existing-user")
        assert result is True

        # User doesn't exist
        mock_response.status_code = 404
        result = team_module.check_user_in_org("non-existent-user")
        assert result is False

        # API error
        mock_get.side_effect = Exception("API Error")
        result = team_module.check_user_in_org("any-user")
        assert result is False

    # Test missing environment variables - negative test
    with patch.dict(os.environ, {"GITHUB_ORG": ""}):
        result = team_module.check_user_in_org("any-user")
        assert result is False


@patch("scripts.process_team_issue.check_repo_in_org")
@patch("scripts.process_team_issue.create_repo_warning_issue")
def test_process_repositories(mock_warning, mock_check_repo):
    """Test processing repositories."""
    # All repos exist
    mock_check_repo.return_value = True
    config = {"repositories": ["existing-repo"], "child_teams": [{"name": "team-child", "repositories": ["old-repo"]}]}

    result = team_module.process_repositories(config, ["new-repo", "another-repo"], 1)

    assert "repositories" in result
    assert "new-repo" in result["repositories"]
    assert "another-repo" in result["repositories"]
    assert "existing-repo" in result["repositories"]
    assert "new-repo" in result["child_teams"][0]["repositories"]

    # Some repos don't exist
    mock_check_repo.side_effect = lambda repo: repo != "invalid-repo"
    result = team_module.process_repositories(config, ["valid-repo", "invalid-repo"], 1)

    assert "valid-repo" in result["repositories"]
    assert "invalid-repo" not in result["repositories"]
    mock_warning.assert_called_once_with("invalid-repo", 1)


@patch("scripts.process_team_issue.requests.post")
def test_comment_on_issue(mock_post, monkeypatch):
    """Test commenting on an issue."""
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")

    # Successful comment
    mock_response = MagicMock()
    mock_response.status_code = 201
    mock_post.return_value = mock_response

    result = team_module.comment_on_issue("test-org/test-repo", 1, "Test message", "fake-token")

    assert result is True
    mock_post.assert_called_once()

    # Test error handling for requests failure
    mock_post.reset_mock()
    mock_post.side_effect = requests.RequestException("Connection error")

    result = team_module.comment_on_issue("test-org/test-repo", 1, "Test message", "fake-token")

    assert result is False
    mock_post.assert_called_once()


def test_check_repo_in_org():
    """Test checking if repository exists in organization."""
    # Set environment variable for all tests
    with patch.dict(os.environ, {"GITHUB_ORG": "test-org", "GITHUB_TOKEN": "fake-token"}):
        # Test repo exists
        with patch("scripts.process_team_issue.requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_get.return_value = mock_response

            result = team_module.check_repo_in_org("existing-repo")
            assert result is True

        # Test repo doesn't exist
        with patch("scripts.process_team_issue.requests.get") as mock_get:
            mock_response = MagicMock()
            mock_response.status_code = 404
            mock_get.return_value = mock_response

            result = team_module.check_repo_in_org("non-existent-repo")
            assert result is False

        # Test API error
        with patch("scripts.process_team_issue.requests.get") as mock_get:
            mock_get.side_effect = Exception("API Error")

            result = team_module.check_repo_in_org("any-repo")
            assert result is False

    # Test missing environment variables - negative test
    with patch.dict(os.environ, {"GITHUB_ORG": ""}):
        result = team_module.check_repo_in_org("any-repo")
        assert result is False


@patch("scripts.process_team_issue.requests.post")
def test_create_user_warning_issue(mock_post, monkeypatch):
    """Test creating warning for non-existent user."""
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.setenv("REPO", "test-org/repo")

    # Test with issue number
    mock_response = MagicMock()
    mock_response.status_code = 201
    mock_post.return_value = mock_response

    result = team_module.create_user_warning_issue("invalid-user", 1)

    # Should call comment_on_issue with appropriate message
    assert result is True
    mock_post.assert_called_once()
    args, kwargs = mock_post.call_args
    assert kwargs["json"]["body"] and "invalid-user" in kwargs["json"]["body"]
    assert "not found" in kwargs["json"]["body"].lower()

    # Test without issue number
    mock_post.reset_mock()
    result = team_module.create_user_warning_issue("another-user")

    # Should log warning and return False without API call
    assert result is False
    mock_post.assert_not_called()

    # Test error handling
    mock_post.reset_mock()
    mock_post.side_effect = Exception("API error")

    result = team_module.create_user_warning_issue("invalid-user", 1)
    assert result is False
    mock_post.assert_called_once()
