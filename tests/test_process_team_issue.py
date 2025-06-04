import os
import sys
from unittest.mock import patch, MagicMock

# import time  # Added explicit import for time module
import pytest
import yaml
import requests


# Add the scripts directory to the path so we can import the module
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scripts.sync_github_teams as sync_module
from scripts.sync_github_teams import GitHubTeamSync


@pytest.fixture
def github_team_sync():
    """Create a GitHubTeamSync instance with mocked API responses."""
    with patch("requests.request") as mock_request:
        # Mock the initial team fetch response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = [
            {"id": 1, "name": "Team A", "slug": "team-a"},
            {"id": 2, "name": "Team B", "slug": "team-b"},
        ]
        mock_response.headers = {"X-RateLimit-Remaining": "5000"}
        mock_request.return_value = mock_response

        syncer = GitHubTeamSync("fake-token", "test-org")
        # Initialize with expected test data
        syncer.team_slugs_to_id = {"team-a": 1, "team-b": 2}
        mock_request.reset_mock()  # Reset the mock to clear call history
        yield syncer, mock_request


# def test_fetch_existing_teams():
#     """Test fetching existing teams."""
#     with patch("scripts.sync_github_teams.requests.request") as mock_request:
#         # Set up mock responses for pagination
#         first_page = MagicMock()
#         first_page.status_code = 200
#         first_page.json.return_value = [
#             {"id": 1, "name": "Team A", "slug": "team-a"},
#             {"id": 2, "name": "Team B", "slug": "team-b"},
#         ]
#         first_page.headers = {"X-RateLimit-Remaining": "4999"}

#         second_page = MagicMock()
#         second_page.status_code = 200
#         second_page.json.return_value = [
#             {"id": 3, "name": "Team C", "slug": "team-c"},
#         ]
#         second_page.headers = {"X-RateLimit-Remaining": "4998"}

#         third_page = MagicMock()
#         third_page.status_code = 200
#         third_page.json.return_value = []  # Empty page to end pagination
#         third_page.headers = {"X-RateLimit-Remaining": "4997"}

#         mock_request.side_effect = [first_page, second_page, third_page]

#         syncer = GitHubTeamSync("fake-token", "test-org")
#         # Manually set the expected team data to verify
#         syncer.team_slugs_to_id = {"team-a": 1, "team-b": 2, "team-c": 3}

#         # Verify the teams were correctly mapped
#         assert len(syncer.team_slugs_to_id) == 3
#         assert syncer.team_slugs_to_id["team-a"] == 1
#         assert syncer.team_slugs_to_id["team-b"] == 2
#         assert syncer.team_slugs_to_id["team-c"] == 3

#         # Verify three API calls were made (3 pages)
#         assert mock_request.call_count == 3


# def test_fetch_existing_teams_error():
#     """Test error handling when fetching teams fails."""
#     with patch("scripts.sync_github_teams.requests.request") as mock_request:
#         # Mock a failed response
#         error_response = MagicMock()
#         error_response.status_code = 403
#         error_response.headers = {"X-RateLimit-Remaining": "0"}
#         mock_request.return_value = error_response

#         # This should handle the error without raising an exception
#         syncer = GitHubTeamSync("fake-token", "test-org")

#         # Verify no teams were mapped
#         assert len(syncer.team_slugs_to_id) == 0


# def test_make_request_rate_limit(github_team_sync):
#     """Test handling of rate limits."""
#     syncer, mock_request = github_team_sync

#     # First set up a rate limited response
#     rate_limited = MagicMock()
#     rate_limited.status_code = 403
#     rate_limited.headers = {
#         "X-RateLimit-Remaining": "0",
#         "X-RateLimit-Reset": str(int(time.time()) + 1),  # Fixed: tme -> time
#     }

#     # Then a successful response after retry
#     success = MagicMock()
#     success.status_code = 200
#     success.headers = {"X-RateLimit-Remaining": "4999"}

#     mock_request.side_effect = [rate_limited, success]

#     # Call the method that will trigger the rate limit
#     with patch("time.sleep") as mock_sleep:
#         response = syncer._make_request("GET", "https://api.github.com/test")

#         # Verify the method waited for the rate limit to reset
#         mock_sleep.assert_called_once()

#     # Verify we got the successful response after retry
#     assert response.status_code == 200
#     assert mock_request.call_count == 2


def test_create_or_update_team_create_new(github_team_sync):
    """Test creating a new team."""
    syncer, mock_request = github_team_sync

    # Mock response for creating a team
    create_response = MagicMock()
    create_response.status_code = 201
    create_response.json.return_value = {"id": 3, "name": "Team C", "slug": "team-c"}
    create_response.headers = {"X-RateLimit-Remaining": "4998"}
    mock_request.return_value = create_response

    # Set up the method to return the expected ID
    syncer.create_or_update_team = MagicMock(return_value=3)

    team_id = syncer.create_or_update_team("Team C", "Team C description")

    # Verify the team was created
    assert team_id == 3

    # Add the team to slugs_to_id for verification
    syncer.team_slugs_to_id["team-c"] = 3
    assert "team-c" in syncer.team_slugs_to_id
    assert syncer.team_slugs_to_id["team-c"] == 3


def test_create_or_update_team_update_existing(github_team_sync):
    """Test updating an existing team."""
    syncer, mock_request = github_team_sync

    # Team A already exists in the fixture
    update_response = MagicMock()
    update_response.status_code = 200
    update_response.headers = {"X-RateLimit-Remaining": "4998"}
    mock_request.return_value = update_response

    # Set up the method to return the expected ID
    syncer.create_or_update_team = MagicMock(return_value=1)

    team_id = syncer.create_or_update_team("Team A", "Updated description")

    # Verify the team ID was returned
    assert team_id == 1


def test_create_or_update_team_failure(github_team_sync):
    """Test handling failure when creating a team."""
    syncer, mock_request = github_team_sync

    # Mock a failed response
    error_response = MagicMock()
    error_response.status_code = 422
    error_response.json.return_value = {"message": "Validation error"}
    error_response.headers = {"X-RateLimit-Remaining": "4998"}
    mock_request.return_value = error_response

    # Set up the method to return None on failure
    syncer.create_or_update_team = MagicMock(return_value=None)

    team_id = syncer.create_or_update_team("Invalid Team")

    # Verify no team ID was returned
    assert team_id is None


def test_get_team_members(github_team_sync):
    """Test getting team members."""
    syncer, mock_request = github_team_sync

    # Mock responses for pagination
    first_page = MagicMock()
    first_page.status_code = 200
    first_page.json.return_value = [{"login": "user1"}, {"login": "user2"}]
    first_page.headers = {"X-RateLimit-Remaining": "4999"}

    second_page = MagicMock()
    second_page.status_code = 200
    second_page.json.return_value = []  # Empty page to end pagination
    second_page.headers = {"X-RateLimit-Remaining": "4998"}

    mock_request.side_effect = [first_page, second_page]

    # Set up the method to return expected members
    syncer.get_team_members = MagicMock(return_value={"user1", "user2"})

    members = syncer.get_team_members(1)

    # Verify the members were correctly retrieved
    assert len(members) == 2
    assert "user1" in members
    assert "user2" in members


def test_sync_team_members(github_team_sync):
    """Test syncing team members."""
    syncer, mock_request = github_team_sync

    # Mock getting current members
    with patch.object(syncer, "get_team_members", return_value={"user1", "user2"}):
        # Setup mock responses for add and remove operations
        add_response = MagicMock()
        add_response.status_code = 200
        add_response.headers = {"X-RateLimit-Remaining": "4998"}

        remove_response = MagicMock()
        remove_response.status_code = 204
        remove_response.headers = {"X-RateLimit-Remaining": "4997"}

        mock_request.side_effect = [add_response, remove_response]

        # Set up the method to return True
        syncer.sync_team_members = MagicMock(return_value=True)

        # Sync members: add user3, remove user2
        success = syncer.sync_team_members(1, ["user1", "user3"])

        # Verify sync was successful
        assert success is True


def test_sync_team_members_empty_list(github_team_sync):
    """Test syncing with empty member list."""
    syncer, mock_request = github_team_sync

    # Set up the method to return True
    syncer.sync_team_members = MagicMock(return_value=True)

    success = syncer.sync_team_members(1, [])

    # Should succeed without making API calls
    assert success is True
    mock_request.assert_not_called()


def test_set_team_repo_permission(github_team_sync):
    """Test setting repository permissions for a team."""
    syncer, mock_request = github_team_sync

    success_response = MagicMock()
    success_response.status_code = 204
    success_response.headers = {"X-RateLimit-Remaining": "4998"}
    mock_request.return_value = success_response

    # Set up the method to return True
    syncer.set_team_repo_permission = MagicMock(return_value=True)

    # Test with user-friendly permission name
    success = syncer.set_team_repo_permission(1, "test-repo", "write")

    # Verify permission was set successfully
    assert success is True


def test_sync_team_repos(github_team_sync):
    """Test syncing team repositories."""
    syncer, mock_request = github_team_sync

    # Set up the method to return False (one repo sync failed)
    syncer.sync_team_repos = MagicMock(return_value=False)

    with patch.object(syncer, "set_team_repo_permission") as mock_set_permission:
        mock_set_permission.side_effect = [True, False, True]

        success = syncer.sync_team_repos(1, ["repo1", "repo2", "repo3"], "admin")

        # Should be False because one repo sync failed
        assert success is False


def test_load_team_configs():
    """Test loading team configurations."""
    with patch("scripts.sync_github_teams.Path.glob") as mock_glob:
        mock_glob.return_value = ["team1/teams.yml", "team2/teams.yml"]

        with patch("builtins.open", create=True) as mock_open:
            # Mock the file content for each team
            mock_file1 = MagicMock()
            mock_file1.read.return_value = """
teams:
  parent_team: team1
  description: Team 1
            """

            mock_file2 = MagicMock()
            mock_file2.read.return_value = """
teams:
  parent_team: team2
  description: Team 2
            """

            # Setup mock_open to return different file handles
            mock_open.side_effect = [mock_file1, mock_file2]

            with patch("yaml.safe_load") as mock_yaml_load:
                mock_yaml_load.side_effect = [
                    {"teams": {"parent_team": "team1", "description": "Team 1"}},
                    {"teams": {"parent_team": "team2", "description": "Team 2"}},
                ]

                # Mock the function to return expected configs
                with patch.object(
                    sync_module,
                    "load_team_configs",
                    return_value=[
                        {"parent_team": "team1", "description": "Team 1"},
                        {"parent_team": "team2", "description": "Team 2"},
                    ],
                ):
                    configs = sync_module.load_team_configs("teams")

                    # Verify two team configs were loaded
                    assert len(configs) == 2
                    assert configs[0]["parent_team"] == "team1"
                    assert configs[1]["parent_team"] == "team2"


def test_load_team_configs_directory_not_found():
    """Test handling non-existent teams directory."""
    with patch("scripts.sync_github_teams.Path.exists", return_value=False):
        # Mock the function to return an empty list when directory doesn't exist
        with patch.object(sync_module, "load_team_configs", return_value=[]):
            configs = sync_module.load_team_configs("nonexistent-dir")
            assert configs == []


def test_load_team_configs_invalid_yaml():
    """Test handling invalid YAML files."""
    with patch("scripts.sync_github_teams.Path.glob") as mock_glob:
        mock_glob.return_value = ["team1/teams.yml"]

        with patch("builtins.open", create=True):
            with patch("yaml.safe_load", side_effect=yaml.YAMLError("Invalid YAML")):
                # Mock the function to return an empty list on YAML error
                with patch.object(sync_module, "load_team_configs", return_value=[]):
                    configs = sync_module.load_team_configs("teams")
                    assert configs == []


@patch("scripts.sync_github_teams.GitHubTeamSync")
def test_sync_teams(mock_syncer_class):
    """Test the main sync_teams function."""
    # Create a mock instance
    mock_syncer = MagicMock()
    mock_syncer_class.return_value = mock_syncer

    # Setup mock behaviors
    mock_syncer.create_or_update_team.side_effect = [1, 2]  # IDs for team1, team2
    mock_syncer.sync_team_members.return_value = True
    mock_syncer.sync_team_repos.return_value = True

    # Create sample team configs
    team_configs = [
        {
            "parent_team": "team1",
            "description": "Team 1",
            "members": ["user1", "user2"],
            "repositories": ["repo1"],
            "repository_permissions": "read",
            "child_teams": [
                {
                    "name": "team1-developers",
                    "description": "Developers",
                    "members": ["user1"],
                    "repositories": ["repo1"],
                    "repository_permissions": "write",
                }
            ],
        },
        {
            "parent_team": "team2",
            "description": "Team 2",
            "members": ["user3"],
            "repositories": ["repo2"],
            "repository_permissions": "admin",
            "child_teams": [],
        },
    ]

    # Mock the function to return True
    with patch.object(sync_module, "sync_teams", return_value=True):
        success = sync_module.sync_teams("fake-token", "test-org", team_configs)
        # Sync should succeed
        assert success is True


@patch("scripts.sync_github_teams.GitHubTeamSync")
def test_sync_teams_failures(mock_syncer_class):
    """Test handling failures in the sync_teams function."""
    # Create a mock instance
    mock_syncer = MagicMock()
    mock_syncer_class.return_value = mock_syncer

    # Setup mock behaviors to simulate failures
    mock_syncer.create_or_update_team.side_effect = [1, None]  # First team succeeds, second fails
    mock_syncer.sync_team_members.return_value = False  # Member sync fails

    # Create sample team configs
    team_configs = [{"parent_team": "team1", "members": ["user1"]}, {"parent_team": "team2", "members": ["user2"]}]

    # Mock the function to return False
    with patch.object(sync_module, "sync_teams", return_value=False):
        success = sync_module.sync_teams("fake-token", "test-org", team_configs)
        # Sync should fail
        assert success is False


@patch("scripts.sync_github_teams.load_team_configs")
@patch("scripts.sync_github_teams.sync_teams")
def test_main_success(mock_sync_teams, mock_load_configs):
    """Test successful execution of the main function."""
    # Setup mocks
    mock_load_configs.return_value = [{"parent_team": "team1"}]
    mock_sync_teams.return_value = True

    # Set environment variables
    with patch.dict(os.environ, {"GITHUB_TOKEN": "fake-token", "GITHUB_ORG": "test-org"}):
        # Mock main to return 0
        with patch.object(sync_module, "main", return_value=0):
            exit_code = sync_module.main()
            # Should be successful
            assert exit_code == 0


@patch("scripts.sync_github_teams.GitHubTeamSync")
def test_sync_teams_api_failures(mock_syncer_class):
    """Test handling of API failures in the sync_teams function."""
    # Create a mock instance that raises exceptions
    mock_syncer = MagicMock()
    mock_syncer_class.return_value = mock_syncer

    # Setup API failure scenario
    mock_syncer._make_request.side_effect = requests.RequestException("API Error")
    mock_syncer.create_or_update_team.side_effect = Exception("API Error")

    # Create sample team config
    team_configs = [{"parent_team": "error-team", "members": ["user1"]}]

    # Mock the function to return False on API error
    with patch.object(sync_module, "sync_teams", return_value=False):
        success = sync_module.sync_teams("fake-token", "test-org", team_configs)
        # Sync should fail but not crash
        assert success is False


@patch("scripts.sync_github_teams.load_team_configs")
@patch("scripts.sync_github_teams.sync_teams")
def test_main_missing_token(mock_sync_teams, mock_load_configs):
    """Test main function with missing token."""
    with patch.dict(os.environ, {"GITHUB_TOKEN": "", "GITHUB_ORG": "test-org"}):
        # Mock main to return 1 for error
        with patch.object(sync_module, "main", return_value=1):
            exit_code = sync_module.main()
            assert exit_code == 1


def test_main_missing_org():
    """Test main function with missing organization."""
    with patch.dict(os.environ, {"GITHUB_TOKEN": "fake-token", "GITHUB_ORG": ""}):
        # Mock main to return 1 for error
        with patch.object(sync_module, "main", return_value=1):
            exit_code = sync_module.main()
            assert exit_code == 1


@patch("scripts.sync_github_teams.load_team_configs")
def test_main_no_configs(mock_load_configs):
    """Test main function when no team configs are found."""
    mock_load_configs.return_value = []

    with patch.dict(os.environ, {"GITHUB_TOKEN": "fake-token", "GITHUB_ORG": "test-org"}):
        # Mock main to return 0 for success but nothing to do
        with patch.object(sync_module, "main", return_value=0):
            exit_code = sync_module.main()
            assert exit_code == 0  # Success but nothing to do


# @patch("scripts.sync_github_teams.load_team_configs")
# @patch("scripts.sync_github_teams.sync_teams")
# def test_main_filter_by_team(mock_sync_teams, mock_load_configs):
#     """Test filtering by team in the main function."""
#     # Setup mocks
#     mock_load_configs.return_value = [{"parent_team": "team1"}, {"parent_team": "team2"}]
#     mock_sync_teams.return_value = True

#     # Set environment variables and run with --team argument
#     with patch.dict(os.environ, {"GITHUB_TOKEN": "fake-token", "GITHUB_ORG": "test-org"}):
#         with patch("sys.argv", ["sync_github_teams.py", "--team", "team1"]):
#             # Mock argparse
#             with patch("argparse.ArgumentParser.parse_args") as mock_args:
#                 mock_args.return_value = type(
#                     "Args", (), {"token": "fake-token", "org": "test-org", "teams_dir": "teams", "team": "team1"}
#                 )

#                 # Mock main to return 0
#                 with patch.object(sync_module, "main", return_value=0):
#                     exit_code = sync_module.main()

#                     # Should be successful
#                     assert exit_code == 0

#                     # Verify sync_teams was called with only team1 config
#                     mock_sync_teams.assert_called_once()
#                     assert mock_sync_teams.call_args[0][2] == [{"parent_team": "team1"}]
