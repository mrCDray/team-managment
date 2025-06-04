import os
import logging
import requests

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("team_utils")


def ensure_team_name_prefix(parent_team: str, child_team: str) -> str:
    """
    Ensures a child team name has the parent team prefix.
    If the child team already has the parent prefix, returns as is.
    Otherwise, adds the parent prefix.

    Args:
        parent_team: The parent team name
        child_team: The child team name or suffix

    Returns:
        Properly formatted team name with parent prefix
    """
    parent_prefix = f"{parent_team}-"

    # If the child team already has the parent prefix, return as is
    if child_team.startswith(parent_prefix):
        return child_team

    # Otherwise, add the prefix
    return f"{parent_prefix}{child_team}"


def check_user_in_org(username: str) -> bool:
    """Check if the user exists in the organization."""
    token = os.environ.get("GITHUB_TOKEN")
    org = os.environ.get("GITHUB_ORG")

    if not org:
        logger.error("GITHUB_ORG environment variable not set")
        return False

    url = f"https://api.github.com/orgs/{org}/members/{username}"
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}

    try:
        response = requests.get(url, headers=headers)
        # 204 indicates the user is a member, 404 indicates they're not
        return response.status_code == 204
    except Exception as e:
        logger.error(f"Error checking if user {username} exists in org: {str(e)}")
        return False


def check_repo_in_org(repo_name: str) -> bool:
    """Check if the repository exists in the organization."""
    token = os.environ.get("GITHUB_TOKEN")
    org = os.environ.get("GITHUB_ORG")

    if not org:
        logger.error("GITHUB_ORG environment variable not set")
        return False

    url = f"https://api.github.com/repos/{org}/{repo_name}"
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}

    try:
        response = requests.get(url, headers=headers)
        # 200 indicates the repository exists, 404 indicates it doesn't
        return response.status_code == 200
    except Exception as e:
        logger.error(f"Error checking if repository {repo_name} exists in org: {str(e)}")
        return False


def comment_on_issue(repo: str, issue_number: int, message: str, token: str) -> bool:
    """Add a comment to the issue."""
    logger.info(f"Commenting on issue #{issue_number}")
    url = f"https://api.github.com/repos/{repo}/issues/{issue_number}/comments"
    headers = {"Authorization": f"token {token}", "Accept": "application/vnd.github.v3+json"}
    data = {"body": message}

    try:
        response = requests.post(url, headers=headers, json=data)
        if response.status_code == 201:
            logger.info("Successfully added comment to issue")
            return True

        logger.error(f"Failed to comment on issue: {response.status_code} - {response.text}")
        return False
    except Exception as e:
        logger.error(f"Exception when commenting on issue: {str(e)}")
        return False
