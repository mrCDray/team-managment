import os
import re
import sys
import json
import logging
from collections import OrderedDict
from pathlib import Path
from typing import List, Dict, Optional, Any, Tuple
import yaml

# Import the team sync functionality
from sync_github_teams import sync_teams

# Import utility functions from the new module
from team_utils import ensure_team_name_prefix, check_user_in_org, check_repo_in_org, comment_on_issue


class IndentDumper(yaml.Dumper):
    def increase_indent(self, flow=False, indentless=False):
        return super().increase_indent(flow, False)


# Add custom representer for lists to ensure proper indentation
def represent_list(dumper, data):
    return dumper.represent_sequence("tag:yaml.org,2002:seq", data, flow_style=False)


yaml.add_representer(list, represent_list)


# Add custom representer for strings to handle multiline content properly
def represent_str(dumper, data):
    style = "|" if "\n" in data else None
    return dumper.represent_scalar("tag:yaml.org,2002:str", data, style=style)


yaml.add_representer(str, represent_str)
# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("team_processor")


# Configure PyYAML to preserve dictionary order
def represent_ordereddict(dumper, data):
    return dumper.represent_mapping("tag:yaml.org,2002:map", data.items())


yaml.add_representer(OrderedDict, represent_ordereddict)

# Add permission mapping dictionary
permission_mapping = {"read": "pull", "write": "push", "admin": "admin", "maintain": "maintain", "triage": "triage"}


# Initialize logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("team_processor")


def parse_issue_body(body: str) -> Dict[str, Optional[Any]]:
    """Parse the issue body to extract input values."""
    logger.info("Parsing issue body")
    lines = body.split("\n")

    action = None
    team_name = None
    project = None
    team_description = None
    members = []
    repositories = []
    child_teams = []

    current_section = None

    for line in lines:
        if "### Action" in line:
            current_section = "action"
            continue
        if "### Team Name" in line:
            current_section = "team_name"
            continue
        if "### Project Name" in line:
            current_section = "project"
            continue
        if "### Team Description" in line:
            current_section = "team_description"
            continue
        if "### Child Teams" in line:
            current_section = "child_teams"
            continue
        if "### Members" in line:
            current_section = "members"
            continue
        if "### Repositories" in line:
            current_section = "repositories"
            continue

        if current_section and line.strip():
            if current_section == "action":
                action = line.strip()
            elif current_section == "team_name":
                team_name = line.strip()
            elif current_section == "project":
                project = line.strip()
            elif current_section == "team_description":
                team_description = line.strip()
            elif current_section == "child_teams" and line.strip().startswith("- "):
                child_teams.append(line.strip())
            elif current_section == "members" and line.strip().startswith("- @"):
                members.append(line.strip())
            elif current_section == "repositories" and line.strip().startswith("- "):
                repositories.append(line.strip()[2:])  # Remove the "- " prefix

    result = {
        "action": action,
        "team_name": team_name,
        "project": project,
        "team_description": team_description,
        "child_teams": child_teams,
        "members": members,
        "repositories": repositories,
    }

    logger.info(f"Parsed issue data: {json.dumps(result, indent=2)}")
    return result


def create_user_warning_issue(username: str, issue_number: int = None) -> bool:
    """Comment on the current issue about a user that doesn't exist in the organization."""
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("REPO")

    message = f"""
## ⚠️ User Not Found Warning

The user **@{username}** does not exist in the organization or does not have access.

Please check the member details before adding them to a team.
"""

    if issue_number:
        # Comment on the current issue instead of creating a new one
        return comment_on_issue(repo, issue_number, message, token)

    logger.warning(f"No issue number provided for user warning about {username}")
    return False


def parse_member_entry(entry: str, issue_number: int = None) -> Tuple[Optional[str], Optional[List[str]]]:
    """Parse a member entry to extract username and team assignments."""
    # Example: @John-Doe_pgh (developers, testers)
    match = re.match(r"^- @([^\s(]+)\s*\(([^)]+)\)$", entry)
    if match:
        username = match.group(1)
        teams = [team.strip() for team in match.group(2).split(",")]

        # Validate user exists in the organization
        if not check_user_in_org(username):
            create_user_warning_issue(username, issue_number)  # Pass the issue_number here
            logger.warning(f"User {username} does not exist in the organization or lacks access")
            return None, None

        logger.debug(f"Parsed member {username} with teams: {teams}")
        return username, teams

    logger.warning(f"Failed to parse member entry: '{entry}'")
    return None, None


def parse_child_team_entry(entry: str, parent_team: str = None) -> Tuple[Optional[str], Optional[str], Optional[str]]:
    """Parse a child team entry to extract team name, description, and permission."""
    # Example: - developers:Team responsible for development
    # Or: - developers
    entry = entry[2:]  # Remove the "- " prefix
    parts = entry.split(":", 2)

    team_name = parts[0].strip()
    description = parts[1].strip() if len(parts) > 1 else None
    # Add default repository permission
    raw_permission = parts[2].strip() if len(parts) > 2 else "pull"

    # If parent_team is provided, ensure the team name has the proper prefix
    if parent_team:
        team_name = ensure_team_name_prefix(parent_team, team_name)

    # Map user-friendly permission names to GitHub API permissions using dict.get
    permission = permission_mapping.get(raw_permission, raw_permission)

    # Validate permission is valid
    valid_permissions = ["pull", "push", "admin", "maintain", "triage"]
    if permission not in valid_permissions:
        logger.warning(f"Invalid permission '{raw_permission}' for team {team_name}, defaulting to 'pull'")
        permission = "pull"

    logger.debug(f"Parsed child team {team_name} with description: {description} and permission: {permission}")
    return team_name, description, permission


def process_team_members(
    config: Dict[str, Any], members: List[str], team_name: str, issue_number: int = None
) -> Dict[str, Any]:
    """Process team members and assign them to appropriate teams."""
    logger.info(f"Processing {len(members)} team members")

    # Initialize with existing members if available, otherwise empty list
    parent_members = list(config.get("members", [])) if config.get("members") else []
    child_team_members = {}

    # Initialize child team members with existing data
    for child in config.get("child_teams", []):
        child_name = child.get("name", "").replace("[team_name]", team_name)
        if child.get("members"):
            child_team_members[child_name] = list(child.get("members", []))

    # Process new members to add
    for entry in members:
        username, teams = parse_member_entry(entry, issue_number)
        if username:
            # Add to parent team if not already there
            if username not in parent_members:
                if not teams:
                    logger.warning(f"No team assignments for user {username}, adding to parent team only")
                    parent_members.append(username)
                    continue

                if "all" in teams:
                    logger.debug(f"Adding {username} to parent and all child teams")
                    parent_members.append(username)
                    # Add to all child teams
                    for child in config.get("child_teams", []):
                        child_team_name = child["name"].replace("[team_name]", team_name)
                        if child_team_name not in child_team_members:
                            child_team_members[child_team_name] = []
                        if username not in child_team_members[child_team_name]:
                            child_team_members[child_team_name].append(username)
                else:
                    logger.debug(f"Adding {username} to parent team and specified child teams: {teams}")
                    parent_members.append(username)
                    # Add to specific child teams
                    for team_suffix in teams:
                        # Handle both prefixed and non-prefixed team names
                        child_team_name = ensure_team_name_prefix(team_name, team_suffix)
                        if child_team_name not in child_team_members:
                            child_team_members[child_team_name] = []
                        if username not in child_team_members[child_team_name]:
                            child_team_members[child_team_name].append(username)
            else:
                # User already in parent team, add to specified child teams if needed
                if teams:
                    if "all" in teams:
                        # Add to all child teams
                        for child in config.get("child_teams", []):
                            child_team_name = child["name"].replace("[team_name]", team_name)
                            if child_team_name not in child_team_members:
                                child_team_members[child_team_name] = []
                            if username not in child_team_members[child_team_name]:
                                child_team_members[child_team_name].append(username)
                    else:
                        # Add to specific child teams
                        for team_suffix in teams:
                            # Handle both prefixed and non-prefixed team names
                            child_team_name = ensure_team_name_prefix(team_name, team_suffix)
                            if child_team_name not in child_team_members:
                                child_team_members[child_team_name] = []
                            if username not in child_team_members[child_team_name]:
                                child_team_members[child_team_name].append(username)

    # Update the config with members
    config["members"] = parent_members if parent_members else None

    # Update child teams with preserved members
    for child in config.get("child_teams", []):
        child_name = child["name"].replace("[team_name]", team_name)
        if child_name in child_team_members:
            child["members"] = child_team_members[child_name]
            logger.debug(f"Updated members for child team {child_name}")

    return config


def process_child_teams(
    config: Dict[str, Any], child_teams_entries: List[str], team_name: str, action: str
) -> Dict[str, Any]:
    """Process child teams to add or update them in the configuration."""
    logger.info(f"Processing {len(child_teams_entries)} child teams")

    # Initialize child teams list if it doesn't exist
    if "child_teams" not in config:
        config["child_teams"] = []

    # For update action, keep track of existing child teams
    existing_child_teams = {}
    if action in ["update", "remove"]:
        for idx, child in enumerate(config["child_teams"]):
            existing_child_teams[child["name"]] = idx

    # Process each child team entry
    for entry in child_teams_entries:
        child_team_name, description, permission = parse_child_team_entry(entry, team_name)
        if not child_team_name:
            logger.warning(f"Invalid child team entry: {entry}")
            continue

        if action == "remove":
            # Remove child team if it exists
            if child_team_name in existing_child_teams:
                idx = existing_child_teams[child_team_name]
                logger.info(f"Removing child team: {child_team_name}")
                config["child_teams"].pop(idx)
                # Update indices after removal
                for name, index in existing_child_teams.items():
                    if index > idx:
                        existing_child_teams[name] = index - 1
                existing_child_teams.pop(child_team_name)
        else:
            # Add or update child team
            if child_team_name in existing_child_teams:
                # Update existing child team - only update description and permission if provided
                idx = existing_child_teams[child_team_name]
                if description:  # Only update description if provided
                    config["child_teams"][idx]["description"] = description
                # Always ensure repository_permissions is set
                config["child_teams"][idx]["repository_permissions"] = permission
                logger.info(f"Updated child team: {child_team_name}")
            else:
                # Add new child team - preserve structure similar to existing teams
                parent_repos = config.get("repositories", []) or []
                child_team = {
                    "name": child_team_name,
                    "description": description,
                    "repository_permissions": permission,  # Add repository permission
                    "members": [],
                    "repositories": parent_repos.copy() if parent_repos else [],
                }
                config["child_teams"].append(child_team)
                logger.info(f"Added new child team: {child_team_name} with permission: {permission}")

    return config


def create_repo_warning_issue(repo_name: str, issue_number: int = None) -> bool:
    """Comment on the current issue about a repository that doesn't exist in the organization."""
    token = os.environ.get("GITHUB_TOKEN")
    repo = os.environ.get("REPO")

    message = f"""
## ⚠️ Repository Not Found Warning

The repository **{repo_name}** does not exist in the organization.

Please check the repository details before adding it to a team.
"""

    if issue_number:
        # Comment on the current issue instead of creating a new one
        return comment_on_issue(repo, issue_number, message, token)

    logger.warning(f"No issue number provided for repo warning about {repo_name}")
    return False


def process_repositories(config: Dict[str, Any], repositories: List[str], issue_number: int = None) -> Dict[str, Any]:
    """Process repositories and add them to the team config."""
    logger.info(f"Adding {len(repositories)} repositories to team config")

    # Add new repositories to existing ones for parent team
    current_repos = config.get("repositories", []) or []
    valid_repos = []

    for repo in repositories:
        # Validate that the repository exists in the organization
        if not check_repo_in_org(repo):
            create_repo_warning_issue(repo, issue_number)  # Pass the issue_number here
            logger.warning(f"Repository {repo} does not exist in the organization")
            continue

        valid_repos.append(repo)

    # Update parent team repositories with valid repos
    for repo in valid_repos:
        if repo not in current_repos:
            current_repos.append(repo)

    config["repositories"] = current_repos

    # Add new valid repositories to child teams, preserving existing ones
    for child in config.get("child_teams", []):
        child_repos = child.get("repositories", []) or []
        for repo in valid_repos:
            if repo not in child_repos:
                child_repos.append(repo)
        child["repositories"] = child_repos
        logger.debug(f"Updated repositories for child team {child['name']}")

    return config


def create_team_config(
    team_name: str,
    project: Optional[str],
    team_description: Optional[str],
    child_teams: List[str],
    members: List[str],
    repositories: List[str],
    issue_number: int = None,
) -> Dict[str, Any]:
    """Create a new team configuration file."""
    logger.info(f"Creating team configuration for '{team_name}'")

    # Load the default team config
    try:
        default_config_path = "default_teams_config.yml"
        if not os.path.exists(default_config_path):
            logger.error(f"Default team config file not found: {default_config_path}")
            raise FileNotFoundError(f"Default team configuration file '{default_config_path}' not found")

        with open(default_config_path, "r", encoding="utf-8") as f:
            default_config = yaml.safe_load(f)
            logger.debug("Successfully loaded default team configuration")

        if not default_config or "teams" not in default_config:
            logger.error("Invalid default team config format - missing 'teams' key")
            raise ValueError("Invalid default team configuration format")
    except Exception as e:
        logger.error(f"Failed to load default team config: {str(e)}")
        raise

    # Use OrderedDict to preserve key order
    config = OrderedDict(default_config.get("teams", {}))
    config["parent_team"] = team_name
    config["project"] = project
    if team_description:
        config["description"] = team_description

    # Process child teams
    if child_teams:
        config = process_child_teams(config, child_teams, team_name, "create")

    # Process members and repositories using helper functions
    if members:
        config = process_team_members(config, members, team_name, issue_number)

    if repositories:
        config = process_repositories(config, repositories, issue_number)

    # Replace placeholders
    try:
        config_str = yaml.dump({"teams": config}, default_flow_style=False, sort_keys=False)
        config_str = config_str.replace("[team_name]", team_name)
        config_str = config_str.replace("[project]", project if project else "")

        final_config = yaml.safe_load(config_str)
        logger.debug("Successfully created team configuration")
        return final_config
    except Exception as e:
        logger.error(f"Error creating team configuration: {str(e)}")
        raise


def update_team_config(
    team_name: str,
    team_description: Optional[str],
    child_teams: List[str],
    members: List[str],
    repositories: List[str],
    issue_number: int = None,
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Update an existing team configuration file."""
    team_dir = f"teams/{team_name}"
    team_file = f"{team_dir}/teams.yml"
    logger.info(f"Updating team configuration for '{team_name}'")

    # Load existing configuration
    config, error = load_existing_config(team_file, team_name)
    if error:
        return None, error

    # Update description only if provided and not empty/None/"_No response_"
    if team_description and team_description.strip() and team_description != "_No response_":
        config["teams"]["description"] = team_description

    # Process child teams only if provided
    if child_teams:
        config["teams"] = process_child_teams(config["teams"], child_teams, team_name, "update")

    # Process new members only if provided
    if members:
        config["teams"] = process_team_members(config["teams"], members, team_name, issue_number)

    # Process new repositories only if provided
    if repositories:
        config["teams"] = process_repositories(config["teams"], repositories, issue_number)

    return config, None


def load_existing_config(team_file: str, team_name: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Load existing team configuration file."""
    if not os.path.exists(team_file):
        logger.error(f"Team configuration file {team_file} does not exist")
        return None, f"Team configuration for {team_name} does not exist."

    try:
        with open(team_file, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        if not config or "teams" not in config:
            logger.error(f"Invalid team config format in {team_file}")
            return None, f"Invalid team configuration format in {team_file}"
        return config, None
    except Exception as e:
        logger.error(f"Failed to load team configuration: {str(e)}")
        return None, f"Failed to load team configuration: {str(e)}"


def remove_team_items(
    team_name: str, child_teams: List[str], members: List[str], repositories: List[str], issue_number: int = None
) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Remove members, child teams, or repositories from a team configuration."""
    team_dir = f"teams/{team_name}"
    team_file = f"{team_dir}/teams.yml"
    logger.info(f"Removing items from team '{team_name}'")

    if not os.path.exists(team_file):
        logger.error(f"Team configuration file {team_file} does not exist")
        return None, f"Team configuration for {team_name} does not exist."

    try:
        with open(team_file, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        if not config or "teams" not in config:
            logger.error(f"Invalid team config format in {team_file}")
            return None, f"Invalid team configuration format in {team_file}"
    except Exception as e:
        logger.error(f"Failed to load team configuration: {str(e)}")
        return None, f"Failed to load team configuration: {str(e)}"

    # Process child teams to remove
    if child_teams:
        config["teams"] = process_child_teams(config["teams"], child_teams, team_name, "remove")

    # Process members to remove
    if members:
        logger.info(f"Processing {len(members)} members for removal")
        if config["teams"].get("members"):  # FIXED: Correctly check for members in teams structure
            parent_members = set(config["teams"]["members"])
            initial_count = len(parent_members)
            child_team_members = {}

            # Initialize child_team_members for ALL child teams, even if they have no members
            for child in config["teams"].get("child_teams", []):
                child_name = child["name"]
                child_team_members[child_name] = set(child.get("members", []))

            for entry in members:
                username, teams = parse_member_entry(entry, issue_number)
                if username and username in parent_members:
                    if not teams or "all" in teams:
                        # Remove from parent and all child teams
                        logger.debug(f"Removing {username} from parent team and all child teams")
                        parent_members.remove(username)
                        for child_name, members_set in child_team_members.items():
                            if username in members_set:
                                members_set.remove(username)
                                logger.debug(f"Removed {username} from child team {child_name}")
                    else:
                        # Remove from specific child teams
                        logger.debug(f"Removing {username} from specific child teams: {teams}")
                        for team_suffix in teams:
                            child_team_name = f"{team_name}-{team_suffix}"
                            if (
                                child_team_name in child_team_members
                                and username in child_team_members[child_team_name]
                            ):
                                child_team_members[child_team_name].remove(username)
                                logger.debug(f"Removed {username} from child team {child_team_name}")
                else:
                    logger.warning(f"Member {username} not found in parent team or parsing failed")

            # Update the config - keep empty lists as [] instead of None
            config["teams"]["members"] = list(parent_members) if parent_members else []
            logger.debug(f"Removed {initial_count - len(parent_members)} members from parent team")

            # Ensure all child teams get their member lists updated
            for child in config["teams"].get("child_teams", []):
                child_name = child["name"]
                if child_name in child_team_members:
                    child["members"] = list(child_team_members[child_name])
                    logger.debug(f"Updated members list for {child_name}: {child['members']}")

    # Process repositories to remove
    if repositories:
        logger.info(f"Removing {len(repositories)} repositories from team config")
        if config["teams"].get("repositories"):
            parent_repos = set(config["teams"]["repositories"])
            initial_count = len(parent_repos)
            parent_repos -= set(repositories)
            # Keep empty list as [] instead of None
            config["teams"]["repositories"] = list(parent_repos)
            logger.debug(f"Removed {initial_count - len(parent_repos)} repositories from parent team")

            for child in config["teams"].get("child_teams", []):
                if child.get("repositories"):
                    child_repos = set(child["repositories"])
                    child_initial = len(child_repos)
                    child_repos -= set(repositories)
                    # Keep empty list as [] instead of None
                    child["repositories"] = list(child_repos)
                    logger.debug(
                        f"Removed {child_initial - len(child_repos)} repositories from child team {child['name']}"
                    )

    return config, None


def validate_required_data(issue_data: Dict[str, Any]) -> List[str]:
    """Validate that the issue data contains required fields."""
    errors = []

    if not issue_data.get("action"):
        errors.append("Missing required field: action")
    elif issue_data["action"] not in ["create", "update", "remove"]:
        errors.append(f"Invalid action: {issue_data['action']}. Must be 'create', 'update', or 'remove'.")

    if not issue_data.get("team_name"):
        errors.append("Missing required field: team name")

    # Project and team_description are only required for create action
    if issue_data.get("action") == "create":
        if not issue_data.get("project"):
            errors.append("Missing required field for 'create' action: project")

    return errors


def get_environment_variables() -> Tuple[int, Dict[str, Any], str, str]:
    """Get and validate environment variables needed for processing."""
    try:
        issue_number = int(os.environ.get("ISSUE_NUMBER"))
        issue_body = json.loads(os.environ.get("ISSUE_BODY"))
        repo = os.environ.get("REPO")
        token = os.environ.get("GITHUB_TOKEN")

        missing = []
        if not issue_number:
            missing.append("ISSUE_NUMBER")
        if not issue_body:
            missing.append("ISSUE_BODY")
        if not repo:
            missing.append("REPO")
        if not token:
            missing.append("GITHUB_TOKEN")

        if missing:
            logger.error(f"Missing required environment variables: {', '.join(missing)}")
            sys.exit(1)

        return issue_number, issue_body, repo, token
    except (ValueError, json.JSONDecodeError, TypeError) as e:
        logger.error(f"Error parsing environment variables: {str(e)}")
        sys.exit(1)


def handle_create_action(
    team_name: str, team_file: str, issue_data: Dict[str, Any], issue_number: int = None
) -> Tuple[Optional[str], Optional[Tuple[Dict[str, Any], str]]]:
    """Handle the 'create' action for team configuration."""
    if os.path.exists(team_file):
        return f"⚠️ Team configuration for {team_name} already exists. Use 'update' action instead.", None

    config = create_team_config(
        team_name,
        issue_data["project"],
        issue_data["team_description"],
        issue_data["child_teams"],
        issue_data["members"],
        issue_data["repositories"],
        issue_number,
    )
    return None, (config, f"✅ Team configuration for {team_name} created successfully.")


def handle_update_action(
    team_name: str, issue_data: Dict[str, Any], issue_number: int = None
) -> Tuple[Optional[str], Optional[Tuple[Dict[str, Any], str]]]:
    """Handle the 'update' action for team configuration."""
    # Only pass non-empty fields for update
    team_description = issue_data.get("team_description")
    if team_description == "_No response_" or not team_description:
        team_description = None

    child_teams = issue_data.get("child_teams", [])
    members = issue_data.get("members", [])
    repositories = issue_data.get("repositories", [])

    config, error = update_team_config(team_name, team_description, child_teams, members, repositories, issue_number)
    if error:
        return f"⚠️ {error}", None

    return None, (config, f"✅ Team configuration for {team_name} updated successfully.")


def handle_remove_action(
    team_name: str, issue_data: Dict[str, Any], issue_number: int = None
) -> Tuple[Optional[str], Optional[Tuple[Dict[str, Any], str]]]:
    """Handle the 'remove' action for team configuration."""
    config, error = remove_team_items(
        team_name, issue_data["child_teams"], issue_data["members"], issue_data["repositories"], issue_number
    )
    if error:
        return f"⚠️ {error}", None

    return None, (config, f"✅ Items removed from team {team_name} configuration successfully.")


def save_team_config(team_file: str, config: Dict[str, Any]) -> bool:
    """Save team configuration to file."""
    try:
        # Ensure the directory exists
        os.makedirs(os.path.dirname(team_file), exist_ok=True)

        # Write configuration to file
        with open(team_file, mode="w", encoding="utf-8") as f:
            yaml.dump(config, f, sort_keys=False, Dumper=IndentDumper, default_flow_style=False, indent=2)

        logger.info(f"Successfully saved team configuration to: {team_file}")
        return True
    except Exception as e:
        logger.error(f"Error saving team configuration: {str(e)}")
        return False


def validate_team_existence(
    team_file: str, team_name: str, action: str, repo: str, issue_number: int, token: str
) -> bool:
    """
    Validate if the team exists based on the requested action.
    Returns True if validation passes, otherwise handles the error and returns False.
    """
    team_exists = os.path.exists(team_file)

    if action == "create" and team_exists:
        error_message = f"⚠️ Team configuration for {team_name} already exists. Use 'update' action instead."
        logger.warning(error_message)
        comment_on_issue(repo, issue_number, error_message, token)
        return False

    if action in ["update", "remove"] and not team_exists:
        error_message = (
            f"⚠️ Team configuration for {team_name} does not exist. Please check the team name or create the team first."
        )
        logger.warning(error_message)
        comment_on_issue(repo, issue_number, error_message, token)
        return False

    return True


def execute_team_action(
    action: str, team_name: str, team_file: str, issue_data: Dict[str, Any], issue_number: int = None
) -> Tuple[Optional[str], Optional[Dict[str, Any]], Optional[str]]:
    """
    Execute the requested team action and return the results.
    Returns a tuple of (error_message, config, response_message).
    """
    error_message = None
    config = None
    response_message = None

    try:
        if action == "create":
            error_message, result = handle_create_action(team_name, team_file, issue_data, issue_number)
            if result:
                config, response_message = result

        elif action == "update":
            error_message, result = handle_update_action(team_name, issue_data, issue_number)
            if result:
                config, response_message = result

        elif action == "remove":
            error_message, result = handle_remove_action(team_name, issue_data, issue_number)
            if result:
                config, response_message = result

        else:
            error_message = f"⚠️ Unknown action: {action}. Use 'create', 'update', or 'remove'."
            logger.error(error_message)

    except FileNotFoundError as e:
        error_message = f"❌ File not found: {str(e)}"
        logger.error(error_message)
    except yaml.YAMLError as e:
        error_message = f"❌ YAML parsing error: {str(e)}"
        logger.error(error_message)
    except Exception as e:
        error_message = f"❌ Error processing team issue: {str(e)}"
        logger.error(f"Error processing team issue: {str(e)}", exc_info=True)

    return error_message, config, response_message


def sync_team_with_github(team_name: str, token: str, org: str) -> Tuple[bool, str]:
    """Sync a specific team with GitHub after configuration changes."""
    logger.info(f"Syncing team {team_name} with GitHub")

    # Load only the specific team configuration
    team_configs = []
    team_file = f"teams/{team_name}/teams.yml"

    if not os.path.exists(team_file):
        error_msg = f"Team configuration file {team_file} not found"
        logger.error(error_msg)
        return False, error_msg

    try:
        with open(team_file, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)
            if config and "teams" in config:
                team_configs.append(config["teams"])
                logger.info(f"Loaded team configuration from {team_file}")
            else:
                error_msg = f"Invalid team configuration in {team_file}"
                logger.warning(error_msg)
                return False, error_msg
    except Exception as e:
        error_msg = f"Failed to load team configuration: {str(e)}"
        logger.error(error_msg)
        return False, error_msg

    # Call the sync_teams function with the loaded configuration
    try:
        # Just call the function without storing the unused result
        sync_teams(token, org, team_configs)
        return True, "Team successfully synchronized with GitHub"
    except Exception as e:
        error_msg = f"Failed to sync team with GitHub: {str(e)}"
        logger.error(error_msg)
        return False, error_msg


def process_team_issue() -> None:
    """Main function to process team management issues."""
    logger.info("Starting team issue processing")

    try:
        # Get environment variables
        issue_number, issue_body, repo, token = get_environment_variables()
        logger.info(f"Processing issue #{issue_number} in repo {repo}")

        # Parse and validate issue data
        issue_data = parse_issue_body(issue_body)
        validation_errors = validate_required_data(issue_data)
        if validation_errors:
            error_message = "⚠️ Validation errors in issue:\n" + "\n".join([f"- {error}" for error in validation_errors])
            logger.error(error_message)
            comment_on_issue(repo, issue_number, error_message, token)
            sys.exit(1)

        # Ensure teams directory exists
        Path("teams").mkdir(exist_ok=True)
        logger.debug("Ensured teams directory exists")

        team_name = issue_data["team_name"]
        action = issue_data["action"]
        team_dir = f"teams/{team_name}"
        team_file = f"{team_dir}/teams.yml"

        # Validate team existence based on action
        if not validate_team_existence(team_file, team_name, action, repo, issue_number, token):
            sys.exit(1)

        # Execute the requested action
        error_message, config, response_message = execute_team_action(
            action, team_name, team_file, issue_data, issue_number
        )

        # Save config if available and no errors
        sync_result_message = ""
        if config and not error_message:
            # Ensure teams directory exists before saving
            os.makedirs(os.path.dirname(team_file), exist_ok=True)
            if not save_team_config(team_file, config):
                error_message = "❌ Error saving team configuration"
            else:
                logger.info(f"Successfully created/updated team file: {team_file}")

                # Sync the team with GitHub after successful save
                org = os.environ.get("GITHUB_ORG")
                if org:
                    sync_success, sync_message = sync_team_with_github(team_name, token, org)
                    sync_result_message = f"\n\n### GitHub Team Synchronization\n{sync_message}"
                    if not sync_success:
                        logger.warning(f"Team sync warning: {sync_message}")
                else:
                    sync_result_message = (
                        "\n\n### GitHub Team Synchronization\nFailed: GITHUB_ORG environment variable not set"
                    )
                    logger.error("GITHUB_ORG environment variable not set, skipping team sync")

        # Comment on the issue
        message = error_message if error_message else response_message
        if sync_result_message and not error_message:
            message += sync_result_message
        comment_on_issue(repo, issue_number, message, token)
        logger.info("Team issue processing completed")

        if error_message:
            sys.exit(1)

    except Exception as e:
        logger.error(f"Unexpected error: {str(e)}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    process_team_issue()
