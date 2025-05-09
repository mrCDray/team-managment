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
        self, method: str, url: str, data: Dict = None, params: Dict = None
    ) -> Optional[requests.Response]:
        """Make a request to GitHub API with rate limit handling."""
        try:
            # Check if we need to wait for rate limit reset
            if self.rate_limit_remaining < 10:
                logger.warning("Approaching GitHub API rate limit, waiting...")
                time.sleep(60)  # Wait a minute to allow rate limit to reset

            response = requests.request(method, url, headers=self.headers, json=data, params=params)

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

        except Exception as e:
            logger.error(f"Error making request to {url}: {str(e)}")
            return None

    def user_exists(self, username: str) -> bool:
        """Check if a user exists in the organization."""
        url = f"{self.base_url}/orgs/{self.org}/members/{username}"
        response = self._make_request("GET", url)
        # Status code 204 means the user is a member, 404 means not a member
        return response and response.status_code == 204

    def repo_exists(self, repo_name: str) -> bool:
        """Check if a repository exists in the organization."""
        url = f"{self.base_url}/repos/{self.org}/{repo_name}"
        response = self._make_request("GET", url)
        return response and response.status_code == 200

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

        # Convert to set for easier comparison
        desired_members_set = set()
        for member in desired_members:
            if self.user_exists(member):
                desired_members_set.add(member)
            else:
                logger.warning(f"User '{member}' does not exist in the organization, skipping")

        # Get current members
        current_members = self.get_team_members(team_id)

        success = True

        # Add members that need to be added
        for member in desired_members_set - current_members:
            logger.info(f"Adding member '{member}' to team ID {team_id}")
            url = f"{self.base_url}/teams/{team_id}/memberships/{member}"
            response = self._make_request("PUT", url, {"role": "member"})

            if not response or response.status_code not in (200, 201):
                logger.error(f"Failed to add member '{member}': {response.status_code if response else 'No response'}")
                success = False

        # Remove members that need to be removed
        for member in current_members - desired_members_set:
            logger.info(f"Removing member '{member}' from team ID {team_id}")
            url = f"{self.base_url}/teams/{team_id}/memberships/{member}"
            response = self._make_request("DELETE", url)

            if not response or response.status_code != 204:
                logger.error(
                    f"Failed to remove member '{member}': {response.status_code if response else 'No response'}"
                )
                success = False

        return success

    def set_team_repo_permission(self, team_id: int, repo_name: str, permission: str) -> bool:
        """Set repository permissions for a team."""
        # Map the permission from config to GitHub API permission
        gh_permission = PERMISSION_MAPPING.get(permission, permission)

        url = f"{self.base_url}/teams/{team_id}/repos/{self.org}/{repo_name}"
        data = {"permission": gh_permission}

        response = self._make_request("PUT", url, data)

        if response and response.status_code == 204:
            logger.info(f"Successfully set {gh_permission} permission for team ID {team_id} on repository {repo_name}")
            return True

        logger.error(f"Failed to set repo permission: {response.status_code if response else 'No response'}")
        return False

    def sync_team_repos(self, team_id: int, repositories: List[str], permission: str) -> bool:
        """Sync repository access for a team."""
        if not repositories:
            logger.info(f"No repositories specified for team ID {team_id}, skipping repo sync")
            return True

        success = True

        for repo in repositories:
            if self.repo_exists(repo):
                if not self.set_team_repo_permission(team_id, repo, permission):
                    success = False
            else:
                logger.warning(f"Repository '{repo}' does not exist in the organization, skipping")

        return success


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
