#!/usr/bin/env python3

import os
import sys
import argparse
import logging
import time
from pathlib import Path
from typing import Dict, List, Optional, Any, Set
import yaml
import requests

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("github_team_sync")

# Define the permission mapping for repository access
PERMISSION_MAPPING = {"read": "pull", "write": "push", "admin": "admin", "maintain": "maintain", "triage": "triage"}


class GitHubTeamSync:
    def __init__(self, token: str, org: str, base_url: str = "https://api.github.com"):
        """Initialize GitHub API client with authentication."""
        self.org = org
        self.base_url = base_url
        self.headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}
        # Initialize team-related dictionaries as proper class attributes
        self.existing_teams = {}
        self.team_slugs_to_id = {}
        self.team_id_to_slug = {}
        self.rate_limit_remaining = 5000  # GitHub API rate limit default

        # Fetch existing teams to avoid unnecessary API calls
        self._fetch_existing_teams()

    def _fetch_existing_teams(self) -> None:
        """Fetch all existing teams in the organization."""
        logger.info(f"Fetching existing teams for organization: {self.org}")

        teams = []
        page = 1
        while True:
            url = f"{self.base_url}/orgs/{self.org}/teams?per_page=100&page={page}"
            response = self._make_request("GET", url)

            if not response or response.status_code != 200:
                logger.error(f"Failed to fetch teams: {response.status_code if response else 'No response'}")
                break

            page_teams = response.json()
            if not page_teams:
                break

            teams.extend(page_teams)
            page += 1

        # Create a mapping of team slugs to IDs
        for team in teams:
            slug = team["slug"]
            team_id = team["id"]
            self.team_slugs_to_id[slug] = team_id
            self.team_id_to_slug[team_id] = slug
            self.existing_teams[slug] = team

        logger.info(f"Fetched {len(teams)} existing teams")

    def _make_request(
        self, method: str, url: str, data: Dict = None, params: Dict = None, headers: Dict = None
    ) -> Optional[requests.Response]:
        """Make a request to GitHub API with rate limit handling."""
        try:
            # Use provided headers or default to self.headers
            request_headers = headers if headers else self.headers

            # Check if we need to wait for rate limit reset
            if self.rate_limit_remaining < 10:
                logger.warning("Approaching GitHub API rate limit, waiting...")
                time.sleep(60)  # Wait a minute to allow rate limit to reset

            response = requests.request(method, url, headers=request_headers, json=data, params=params)

            # Update rate limit information
            if "X-RateLimit-Remaining" in response.headers:
                self.rate_limit_remaining = int(response.headers["X-RateLimit-Remaining"])

            if response.status_code == 403 and "X-RateLimit-Reset" in response.headers:
                reset_time = int(response.headers["X-RateLimit-Reset"])
                wait_time = max(0, reset_time - time.time()) + 1
                logger.warning(f"Rate limited. Waiting {wait_time:.0f} seconds.")
                time.sleep(wait_time)
                # Retry the request
                return self._make_request(method, url, data, params)

            return response

        except requests.exceptions.Timeout:
            logger.error(f"Request to {url} timed out")
            return None
        except requests.exceptions.ConnectionError:
            logger.error(f"Connection error when making request to {url}")
            return None
        except Exception as e:
            logger.error(f"Error making request to {url}: {str(e)}", exc_info=True)
            return None

    def user_exists(self, username: str) -> bool:
        """Check if a user exists in the organization."""
        url = f"{self.base_url}/orgs/{self.org}/members/{username}"
        response = self._make_request("GET", url)
        # Status code 204 means the user is a member, 404 means not a member
        return response and response.status_code == 204

    def repo_exists(self, repo_name: str) -> tuple:
        """Check if a repository exists in the organization and whether it's archived.

        Returns:
            tuple: (exists, is_archived)
        """
        url = f"{self.base_url}/repos/{self.org}/{repo_name}"
        response = self._make_request("GET", url)

        if response and response.status_code == 200:
            repo_data = response.json()
            is_archived = repo_data.get("archived", False)
            return True, is_archived

        return False, False

    def create_or_update_team(self, name: str, description: str = None, parent_id: int = None) -> Optional[int]:
        """Create a new team or update an existing one."""
        team_slug = name.lower()

        if team_slug in self.team_slugs_to_id:
            team_id = self.team_slugs_to_id[team_slug]
            logger.info(f"Team '{name}' already exists with ID {team_id}, updating...")

            # Update team details
            url = f"{self.base_url}/teams/{team_id}"
            data = {"name": name}
            if description:
                data["description"] = description
            if parent_id:
                data["parent_team_id"] = parent_id

            response = self._make_request("PATCH", url, data)
            if response and response.status_code == 200:
                logger.info(f"Successfully updated team '{name}'")
                return team_id

            logger.error(f"Failed to update team '{name}': {response.status_code if response else 'No response'}")
            return None

        # Create a new team
        logger.info(f"Creating new team '{name}'")
        url = f"{self.base_url}/orgs/{self.org}/teams"
        data = {
            "name": name,
            "privacy": "closed",  # Default to closed visibility
        }
        if description:
            data["description"] = description
        if parent_id:
            data["parent_team_id"] = parent_id

        response = self._make_request("POST", url, data)

        if response and response.status_code == 201:
            team_data = response.json()
            team_id = team_data["id"]
            slug = team_data["slug"]
            self.team_slugs_to_id[slug] = team_id
            self.team_id_to_slug[team_id] = slug
            logger.info(f"Successfully created team '{name}' with ID {team_id}")
            return team_id

        error_msg = response.json() if response and response.status_code != 201 else "No response"
        logger.error(f"Failed to create team '{name}': {error_msg}")
        return None

    def get_team_members(self, team_id: int) -> Set[str]:
        """Get the list of members for a team."""
        members = set()
        page = 1

        while True:
            url = f"{self.base_url}/teams/{team_id}/members?per_page=100&page={page}"
            response = self._make_request("GET", url)

            if not response or response.status_code != 200:
                logger.error(f"Failed to get team members: {response.status_code if response else 'No response'}")
                break

            page_members = response.json()
            if not page_members:
                break

            for member in page_members:
                members.add(member["login"])

            page += 1

        return members

    def sync_team_members(self, team_id: int, desired_members: List[str]) -> bool:
        """Sync the team members to match the desired list."""
        if not desired_members:
            logger.info(f"No members specified for team ID {team_id}, skipping member sync")
            return True

        # Store original case for API calls but convert to lowercase for comparison
        desired_members_map = {}  # Maps lowercase to original case
        desired_members_set = set()  # Lowercase for comparison

        for member in desired_members:
            lowercase_name = member.lower()
            desired_members_map[lowercase_name] = member  # Store original case

            if self.user_exists(member):
                desired_members_set.add(lowercase_name)  # Add lowercase to comparison set
            else:
                logger.warning(f"User '{member}' does not exist in the organization, skipping")

        # Get current members and convert to lowercase for comparison
        current_members_raw = self.get_team_members(team_id)
        current_members = {m.lower() for m in current_members_raw}
        current_members_map = {m.lower(): m for m in current_members_raw}

        success = True

        # Add members that need to be added
        for lowercase_member in desired_members_set - current_members:
            original_member = desired_members_map[lowercase_member]
            logger.info(f"Adding member '{original_member}' to team ID {team_id}")
            url = f"{self.base_url}/teams/{team_id}/memberships/{original_member}"
            response = self._make_request("PUT", url, {"role": "member"})

            if not response or response.status_code not in (200, 201):
                logger.error(
                    f"Failed to add member '{original_member}': {response.status_code if response else 'No response'}"
                )
                success = False

        # Remove members that need to be removed
        for lowercase_member in current_members - desired_members_set:
            original_member = current_members_map[lowercase_member]
            logger.info(f"Removing member '{original_member}' from team ID {team_id}")
            url = f"{self.base_url}/teams/{team_id}/memberships/{original_member}"
            response = self._make_request("DELETE", url)

            if not response or response.status_code != 204:
                logger.error(
                    f"Failed to remove member '{original_member}': {response.status_code if response else 'No response'}"
                )
                success = False

        return success

    def set_team_repo_permission(self, team_id: int, repo_name: str, permission: str) -> bool:
        """Set repository permissions for a team."""

        url = f"{self.base_url}/teams/{team_id}/repos/{self.org}/{repo_name}"
        data = {"permission": permission}

        response = self._make_request("PUT", url, data)

        if not response:
            logger.error(f"Failed to set repo permission: No response")
            return False

        # Handle different status codes
        if response.status_code == 204:
            logger.info(f"Successfully set {permission} permission for team ID {team_id} on repository {repo_name}")
            return True
        if response.status_code == 200:
            # Also a success case - GitHub sometimes returns 200 with repository permissions in body
            logger.info(
                f"Successfully set {permission} permission for team ID {team_id} on repository {repo_name} (200 response)"
            )
            return True
        if response.status_code == 404:
            # Specific error for 404 - team doesn't have access or repository doesn't exist
            logger.error(
                f"Failed to set repo permission: Team {team_id} does not have access to repository {repo_name} or the repository doesn't exist"
            )
            return False
            # General error for other status codes
        logger.error(f"Failed to set repo permission: Unexpected status code {response.status_code}")
        return False

    def sync_team_repos(self, team_id: int, repositories: List[str], permission: str) -> bool:
        """Sync repository access for a team."""
        if not repositories:
            logger.info(f"No repositories specified for team ID {team_id}, skipping repo sync")
            return True

        # Map the permission from config to GitHub API permission
        gh_permission = PERMISSION_MAPPING.get(permission, permission)
        success = True

        for repo in repositories:
            exists, is_archived = self.repo_exists(repo)

            if not exists:
                logger.warning(f"Repository '{repo}' does not exist in the organization, skipping")
                continue

            if is_archived:
                logger.warning(f"Repository '{repo}' is archived, skipping permission updates")
                continue

            # Check current permission before attempting to update
            current_permission = self.get_team_repo_permission(team_id, repo)
            logger.debug(
                f"Team ID {team_id} has {current_permission} permissions; trying to set {gh_permission} permission on repository {repo}"
            )
            if current_permission in (gh_permission, "unknown"):
                logger.info(
                    f"Team ID {team_id} already has {current_permission} permission on repository {repo}, skipping"
                )
                continue

            if not self.set_team_repo_permission(team_id, repo, gh_permission):
                success = False

        return success

    def get_team_repo_permission(self, team_id: int, repo_name: str) -> Optional[str]:
        """Get current repository permission for a team."""
        url = f"{self.base_url}/teams/{team_id}/repos/{self.org}/{repo_name}"
        permission = None

        # First try with standard headers
        response = self._make_request("GET", url)

        if response is None:
            logger.warning(f"No response received when checking permissions for team {team_id} on repo {repo_name}")
            return None

        logger.debug(f"Response code returned {response.status_code}")

        # Helper function to extract permission from response data
        def extract_permission_from_data(data):
            # First try to get role_name (direct permission level)
            if "role_name" in data:
                perm = data.get("role_name")
                logger.debug(f"Permission from role_name: {perm}")
                return perm

            # If no role_name, check the permissions object
            if "permissions" in data:
                perms = data.get("permissions", {})

                # Determine highest permission level using ordered list
                for level in ["admin", "maintain", "push", "triage", "pull"]:
                    if perms.get(level):
                        return level

            return None

        if response.status_code == 200:
            permission = extract_permission_from_data(response.json())

        elif response.status_code == 204:
            # For 204 responses, make a second request with repository media type
            logger.debug(f"Team ID {team_id} has access to repository {repo_name}, making second request")

            try:
                headers = {
                    "Authorization": f"token {self.headers['Authorization'].split(' ')[1]}",
                    "Accept": "application/vnd.github.v3.repository+json",
                }
                detailed_response = self._make_request("GET", url, headers=headers)

                if detailed_response and detailed_response.status_code == 200:
                    permission = extract_permission_from_data(detailed_response.json())
            except Exception as e:
                logger.error(f"Error getting detailed permission: {str(e)}")

            # If we couldn't get specific details but we know access exists, use "unknown"
            if permission is None:
                permission = "unknown"

        elif response.status_code == 404:
            # Team doesn't have access to this repository
            logger.debug(f"Team ID {team_id} does not have access to repository {repo_name}")
        else:
            logger.warning(f"Failed to get permission for team {team_id} on repo {repo_name}: {response.status_code}")

        return permission


def load_team_configs(base_path: str = "teams") -> List[Dict[str, Any]]:
    """Load all team configurations from YAML files."""
    team_configs = []
    base_dir = Path(base_path)

    if not base_dir.exists():
        logger.error(f"Teams directory not found: {base_path}")
        return team_configs

    # Find all teams.yml files
    for config_file in base_dir.glob("*/teams.yml"):
        try:
            with open(config_file, "r", encoding="utf-8") as f:
                config = yaml.safe_load(f)
                if config and "teams" in config:
                    team_configs.append(config["teams"])
                    logger.info(f"Loaded team configuration from {config_file}")
                else:
                    logger.warning(f"Invalid team configuration in {config_file}")
        except Exception as e:
            logger.error(f"Failed to load {config_file}: {str(e)}")

    return team_configs


def sync_teams(token: str, org: str, team_configs: List[Dict[str, Any]]) -> bool:
    """Synchronize GitHub teams with the provided configurations."""
    syncer = GitHubTeamSync(token, org)
    success = True

    # First pass: Create or update parent teams
    parent_team_ids = {}
    for config in team_configs:
        parent_team_name = config.get("parent_team")
        if not parent_team_name:
            logger.warning("Team configuration missing parent_team name, skipping")
            continue

        parent_team_id = syncer.create_or_update_team(name=parent_team_name, description=config.get("description"))

        if parent_team_id:
            parent_team_ids[parent_team_name] = parent_team_id
        else:
            logger.error(f"Failed to create/update parent team: {parent_team_name}")
            success = False
            continue

        # Sync members for parent team
        if config.get("members"):
            if not syncer.sync_team_members(parent_team_id, config["members"]):
                logger.error(f"Failed to sync members for parent team {parent_team_name}")
                success = False

        # Sync repositories for parent team
        if config.get("repositories"):
            permission = config.get("repository_permissions", "read")
            if not syncer.sync_team_repos(parent_team_id, config["repositories"], permission):
                logger.error(f"Failed to sync repositories for parent team {parent_team_name}")
                success = False

    # Second pass: Create or update child teams
    for config in team_configs:
        parent_team_name = config.get("parent_team")
        if not parent_team_name or parent_team_name not in parent_team_ids:
            continue

        parent_team_id = parent_team_ids[parent_team_name]

        # Process child teams
        child_teams = config.get("child_teams", [])
        for child in child_teams:
            child_name = child.get("name")
            if not child_name:
                logger.warning("Child team missing name, skipping")
                continue

            child_team_id = syncer.create_or_update_team(
                name=child_name, description=child.get("description"), parent_id=parent_team_id
            )

            if not child_team_id:
                logger.error(f"Failed to create/update child team: {child_name}")
                success = False
                continue

            # Sync members for child team
            if child.get("members"):
                if not syncer.sync_team_members(child_team_id, child["members"]):
                    logger.error(f"Failed to sync members for child team {child_name}")
                    success = False

            # Sync repositories for child team
            if child.get("repositories"):
                permission = child.get("repository_permissions", "read")
                if not syncer.sync_team_repos(child_team_id, child["repositories"], permission):
                    logger.error(f"Failed to sync repositories for child team {child_name}")
                    success = False

    return success


def main():
    """Main function to run the script."""
    parser = argparse.ArgumentParser(description="Synchronize GitHub teams from YAML configurations")
    parser.add_argument("--token", help="GitHub API token", default=os.environ.get("GITHUB_TOKEN"))
    parser.add_argument("--org", help="GitHub organization name", default=os.environ.get("GITHUB_ORG"))
    parser.add_argument("--teams-dir", help="Directory containing team YAML files", default="teams")
    parser.add_argument("--team", help="Specific team to sync (optional)")

    args = parser.parse_args()

    if not args.token:
        logger.error("GitHub token is required. Set GITHUB_TOKEN environment variable or use --token")
        return 1

    if not args.org:
        logger.error("GitHub organization is required. Set GITHUB_ORG environment variable or use --org")
        return 1

    # Load all team configurations
    team_configs = load_team_configs(args.teams_dir)

    if not team_configs:
        logger.warning("No team configurations found")
        return 0

    logger.info(f"Loaded {len(team_configs)} team configurations")

    # Filter to specific team if requested
    if args.team:
        team_configs = [config for config in team_configs if config.get("parent_team") == args.team]
        logger.info(f"Filtered to {len(team_configs)} team configurations for team {args.team}")

        if not team_configs:
            logger.warning(f"No configuration found for team {args.team}")
            return 0

    # Sync teams with GitHub
    if sync_teams(args.token, args.org, team_configs):
        logger.info("Team synchronization completed successfully")
        return 0

    logger.error("Team synchronization completed with errors")
    return 1


if __name__ == "__main__":
    sys.exit(main())
