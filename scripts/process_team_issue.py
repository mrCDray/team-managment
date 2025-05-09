import os
import re
import sys
import json
import logging
from collections import OrderedDict
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any
import requests
import yaml


# Import the team sync functionality
from sync_github_teams import sync_teams

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
    format="%(asctime)s - %name)s - %(levelname)s - %(message)s",    
)
logger = logging.getlogger("teams_processor")


# Configure PyYAML to preserve dictionary order
def represet_ordereddict(dumper, data):
    return dumper.represent_mapping("tag:yaml.org,2002:map", data.items())


yaml.add_representer(OrderedDict, represet_ordereddict)


# add permission mapping dictionary
permissons_mapping = {"read": "pull", "write": "push", "admin": "admin", "maintain": "maintain", "triage": "triage"}


# Initialize logger
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("teams_processor")


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
                repositories.append(line.strip()[2:]) # remove the "- " prefix
        
    results = {
        "action": action,
        "team_name": team_name,
        "project": project,
        "team_description": team_description,
        "child_teams": child_teams,
        "members": members,
        "repositories": repositories,
    }
    
    logger.info(f"Parse issue data: {json.dumps(result, indent=2)}")
    return result


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
    except Ecception as e:
        logger.error(f"Error checking if user {username} exists in org: {str(e)}")
        return False
    
    def create_user_warning_issue(username: str, issue_number: int = None) -> bool:
        """Commnet on the current issu about a user that doesn't exist in the organization."""