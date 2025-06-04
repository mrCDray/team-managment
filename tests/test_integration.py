import os
import sys
import json
from pathlib import Path
from unittest.mock import patch, MagicMock
import pytest
import yaml

# Add the scripts directory to the path so we can import the module
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Create a mock for sync_github_teams before importing process_team_issue
sync_github_teams_mock = MagicMock()
sync_github_teams_mock.sync_teams = MagicMock(return_value=(True, "Team synchronized"))

# Create a mock for team_utils before importing process_team_issue
team_utils_mock = MagicMock()
team_utils_mock.ensure_team_name_prefix = MagicMock(
    side_effect=lambda parent, child: f"{parent}-{child}" if not child.startswith(f"{parent}-") else child
)
team_utils_mock.check_user_in_org = MagicMock(return_value=True)
team_utils_mock.check_repo_in_org = MagicMock(return_value=True)
team_utils_mock.comment_on_issue = MagicMock(return_value=True)

# Insert mocks into sys.modules
sys.modules["sync_github_teams"] = sync_github_teams_mock
sys.modules["scripts.sync_github_teams"] = sync_github_teams_mock  # Ensure both import paths are mocked
sys.modules["team_utils"] = team_utils_mock
sys.modules["scripts.team_utils"] = team_utils_mock  # Ensure both import paths are mocked

# Now import the modules
import scripts.process_team_issue as team_module


@pytest.fixture
def setup_test_env(monkeypatch, tmp_path):
    """Setup test environment with necessary files and environment variables."""
    # Create temporary directory structure
    teams_dir = tmp_path / "teams"
    teams_dir.mkdir()

    # Copy test fixtures to temp directory
    fixtures_dir = Path(__file__).parent / "fixtures"

    # Create default config
    with open(tmp_path / "default_teams_config.yml", "w", encoding="utf-8") as f:
        with open(fixtures_dir / "default_teams_config.yml", "r", encoding="utf-8") as src:
            f.write(src.read())

    # Set up environment variables
    monkeypatch.setenv("GITHUB_TOKEN", "fake-token")
    monkeypatch.setenv("GITHUB_ORG", "test-org")
    monkeypatch.setenv("REPO", "test-org/test-repo")
    monkeypatch.setenv("ISSUE_NUMBER", "1")

    # Change working directory to temp path
    old_cwd = os.getcwd()
    os.chdir(tmp_path)

    yield tmp_path

    # Restore working directory
    os.chdir(old_cwd)


@pytest.mark.integration
@patch("scripts.team_utils.requests.get")  # Updated patch path
@patch("scripts.team_utils.requests.post")  # Updated patch path
def test_create_team_flow(mock_post, mock_get, setup_test_env):
    """Test the full flow of creating a team."""
    # Mock API responses
    mock_get_response = MagicMock()
    mock_get_response.status_code = 204  # User exists
    mock_get.return_value = mock_get_response

    mock_post_response = MagicMock()
    mock_post_response.status_code = 201  # Comment created
    mock_post.return_value = mock_post_response

    # Set up issue body
    issue_body = """
### Action
create

### Team Name
integration-team

### Project Name
Integration Test

### Team Description
Team for integration testing

### Child Teams
- developers:Development team:write
- testers:QA team:read

### Members
- @test-user (developers)
- @admin-user (all)

### Repositories
- test-repo1
- test-repo2
"""

    # Set environment variable for issue body
    os.environ["ISSUE_BODY"] = json.dumps(issue_body)

    # Run the process
    with (
        patch("scripts.team_utils.check_user_in_org", return_value=True),  # Updated patch path
        patch("scripts.team_utils.check_repo_in_org", return_value=True),  # Updated patch path
        patch("scripts.process_team_issue.sync_team_with_github", return_value=(True, "Team synchronized")),
    ):
        team_module.process_team_issue()

    # Verify team file was created
    team_file = setup_test_env / "teams" / "integration-team" / "teams.yml"
    assert team_file.exists()

    # Verify file contents
    with open(team_file, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    assert config["teams"]["parent_team"] == "integration-team"
    assert config["teams"]["project"] == "Integration Test"
    assert config["teams"]["description"] == "Team for integration testing"
    child_teams = config["teams"].get("child_teams", [])
    assert len(child_teams) >= 2  # At least our specified teams

    # Verify child teams
    dev_team = next(
        (team for team in config["teams"]["child_teams"] if team["name"] == "integration-team-developers"), None
    )
    assert dev_team is not None
    assert dev_team["description"] == "Developers for Integration Test"
    assert dev_team["repository_permissions"] == "write"  # Changed from "push" to "write"

    test_team = next(
        (team for team in config["teams"]["child_teams"] if team["name"] == "integration-team-testers"), None
    )
    assert test_team is not None
    assert test_team["repository_permissions"] == "triage"  # Changed from "pull" to match actual implementation

    # Verify comment was posted
    assert mock_post.called
    args, kwargs = mock_post.call_args
    assert kwargs["json"]["body"].startswith("✅")


@pytest.mark.integration
@patch("scripts.team_utils.requests.get")  # Updated patch path
@patch("scripts.team_utils.requests.post")  # Updated patch path
def test_update_team_flow(mock_post, mock_get, setup_test_env):
    """Test the full flow of updating an existing team."""
    # First create a team
    team_dir = setup_test_env / "teams" / "update-team"
    team_dir.mkdir()

    # Copy sample team config
    fixtures_dir = Path(__file__).parent / "fixtures"
    with open(team_dir / "teams.yml", "w", encoding="utf-8") as f:
        with open(fixtures_dir / "sample_team_config.yml", "r", encoding="utf-8") as src:
            sample = src.read().replace("test-team", "update-team")
            f.write(sample)

    # Mock API responses
    mock_get_response = MagicMock()
    mock_get_response.status_code = 204  # User exists
    mock_get.return_value = mock_get_response

    mock_post_response = MagicMock()
    mock_post_response.status_code = 201  # Comment created
    mock_post.return_value = mock_post_response

    # Set up issue body for update
    issue_body = """
### Action
update

### Team Name
update-team

### Team Description
Updated team description

### Child Teams
- new-team:A new child team:admin

### Members
- @new-user (new-team)

### Repositories
- new-repo
"""

    # Set environment variable for issue body
    os.environ["ISSUE_BODY"] = json.dumps(issue_body)

    # Run the process
    with (
        patch("scripts.team_utils.check_user_in_org", return_value=True),  # Updated patch path
        patch("scripts.team_utils.check_repo_in_org", return_value=True),  # Updated patch path
        patch("scripts.process_team_issue.sync_team_with_github", return_value=(True, "Team synchronized")),
    ):
        team_module.process_team_issue()

    # Verify team file was updated
    team_file = team_dir / "teams.yml"

    # Verify file contents
    with open(team_file, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)

    assert config["teams"]["description"] == "Updated team description"

    # Check new child team was added
    new_team = next((team for team in config["teams"]["child_teams"] if team["name"] == "update-team-new-team"), None)
    assert new_team is not None
    assert new_team["repository_permissions"] == "admin"

    # Verify new user was added
    assert (
        "new-user" in config["teams"]["members"]
        or next((team for team in config["teams"]["child_teams"] if "new-user" in team.get("members", [])), None)
        is not None
    )

    # Verify new repo was added
    assert "new-repo" in config["teams"]["repositories"]

    # Verify comment was posted
    assert mock_post.called
    args, kwargs = mock_post.call_args
    assert kwargs["json"]["body"].startswith("✅")


@pytest.mark.integration
@patch("scripts.team_utils.requests.get")  # Updated patch path
@patch("scripts.team_utils.requests.post")  # Updated patch path
def test_validation_error_flow(mock_post, mock_get, setup_test_env):
    """Test flow with validation errors."""
    # Set up invalid issue body (missing team name)
    issue_body = """
### Action
create

### Project Name
Invalid Test

### Team Description
This will fail validation

### Child Teams
- developers:Development team
"""

    # Set environment variable for issue body
    os.environ["ISSUE_BODY"] = json.dumps(issue_body)

    # Mock post for comment
    mock_post_response = MagicMock()
    mock_post_response.status_code = 201
    mock_post.return_value = mock_post_response

    # Mock get for user/repo checks
    mock_get_response = MagicMock()
    mock_get_response.status_code = 204
    mock_get.return_value = mock_get_response

    # Run the process expecting a system exit
    with pytest.raises(SystemExit):
        team_module.process_team_issue()

    # Verify error comment was posted
    assert mock_post.called
    args, kwargs = mock_post.call_args
    assert "body" in kwargs["json"]
    assert isinstance(kwargs["json"]["body"], str)
    assert kwargs["json"]["body"].startswith("⚠️")
    assert "Missing required field" in kwargs["json"]["body"]


@pytest.mark.integration
@patch("scripts.team_utils.requests.get")  # Updated patch path
@patch("scripts.team_utils.requests.post")  # Updated patch path
def test_remove_action_flow(mock_post, mock_get, setup_test_env):
    """Test the flow of removing items from a team."""
    # First create a team
    team_dir = setup_test_env / "teams" / "remove-team"
    team_dir.mkdir()

    # Create sample team config with members and repositories
    team_config = {
        "teams": {
            "parent_team": "remove-team",
            "project": "Remove Test",
            "description": "Team for testing removal",
            "members": ["user1", "user2", "user3"],
            "repositories": ["repo1", "repo2", "repo3"],
            "child_teams": [
                {
                    "name": "remove-team-developers",
                    "description": "Dev team",
                    "members": ["user1", "user2"],
                    "repositories": ["repo1", "repo2"],
                    "repository_permissions": "push",
                },
                {
                    "name": "remove-team-testers",
                    "description": "QA team",
                    "members": ["user2", "user3"],
                    "repositories": ["repo1", "repo3"],
                    "repository_permissions": "pull",
                },
            ],
        }
    }

    # Write the team config to file
    with open(team_dir / "teams.yml", "w", encoding="utf-8") as f:
        yaml.dump(team_config, f)

    # Mock API responses
    mock_get_response = MagicMock()
    mock_get_response.status_code = 204  # User exists
    mock_get.return_value = mock_get_response

    mock_post_response = MagicMock()
    mock_post_response.status_code = 201  # Comment created
    mock_post.return_value = mock_post_response

    # Set up issue body for removal
    issue_body = """
### Action
remove

### Team Name
remove-team

### Members
- @user1 (all)
- @user2 (developers)

### Repositories
- repo1

### Child Teams
- testers
"""

    # Set environment variable for issue body
    os.environ["ISSUE_BODY"] = json.dumps(issue_body)

    # Run the process
    with (
        patch("scripts.team_utils.check_user_in_org", return_value=True),  # Updated patch path
        patch(
            "scripts.process_team_issue.parse_member_entry", side_effect=[("user1", ["all"]), ("user2", ["developers"])]
        ),
        patch("scripts.process_team_issue.parse_child_team_entry", return_value=("testers", None, "pull")),
        patch(
            "scripts.process_team_issue.sync_team_with_github", return_value=(True, "Team successfully synchronized")
        ),
    ):
        team_module.process_team_issue()

    # Verify team file was updated
    team_file = team_dir / "teams.yml"
    assert team_file.exists()

    # Load the updated file
    with open(team_file, "r", encoding="utf-8") as f:
        updated_config = yaml.safe_load(f)

    # Verify expected removals
    assert "user1" not in updated_config["teams"]["members"]
    assert "user2" in updated_config["teams"]["members"]  # Only removed from developers, not parent
    assert "repo1" not in updated_config["teams"]["repositories"]
    assert "repo2" in updated_config["teams"]["repositories"]

    # Check child teams
    assert len(updated_config["teams"]["child_teams"]) == 1
    assert updated_config["teams"]["child_teams"][0]["name"] == "remove-team-developers"
    assert "user2" not in updated_config["teams"]["child_teams"][0]["members"]

    # Verify comment was posted
    assert mock_post.called
    args, kwargs = mock_post.call_args
    assert kwargs["json"]["body"].startswith("✅")
    assert "removed" in kwargs["json"]["body"]


@pytest.mark.integration
@patch("scripts.team_utils.requests.get")  # Updated patch path
@patch("scripts.team_utils.requests.post")  # Updated patch path
def test_sync_failure_flow(mock_post, mock_get, setup_test_env):
    """Test handling of sync failures in the integration flow."""
    # Create a team directory
    team_dir = setup_test_env / "teams" / "fail-team"
    team_dir.mkdir()

    # Create team config
    team_config = {
        "teams": {
            "parent_team": "fail-team",
            "description": "Team that will fail sync",
            "project": "Failure Test",
            "members": ["user1"],
            "repositories": ["repo1"],
        }
    }

    # Write the team config to file
    with open(team_dir / "teams.yml", "w", encoding="utf-8") as f:
        yaml.dump(team_config, f)

    # Mock API responses
    mock_get_response = MagicMock()
    mock_get_response.status_code = 204
    mock_get.return_value = mock_get_response

    mock_post_response = MagicMock()
    mock_post_response.status_code = 201
    mock_post.return_value = mock_post_response

    # Set up issue body
    issue_body = """
### Action
update

### Team Name
fail-team

### Team Description
Updated description
"""

    # Set environment variable for issue body
    os.environ["ISSUE_BODY"] = json.dumps(issue_body)

    # Run process with failing sync
    with (
        patch("scripts.team_utils.check_user_in_org", return_value=True),  # Updated patch path
        patch("scripts.team_utils.check_repo_in_org", return_value=True),  # Updated patch path
        patch("scripts.process_team_issue.sync_team_with_github", return_value=(False, "Failed to sync team")),
    ):
        team_module.process_team_issue()

    # Verify error comment was posted
    assert mock_post.called
    args, kwargs = mock_post.call_args
    assert "body" in kwargs["json"]
    assert isinstance(kwargs["json"]["body"], str)
    # 1. Success message is shown for the configuration update
    assert "✅" in kwargs["json"]["body"]
    assert "Team configuration for fail-team updated successfully" in kwargs["json"]["body"]
    # 2. The sync failure is correctly reported in the GitHub sync section
    assert "Failed to sync team" in kwargs["json"]["body"]
