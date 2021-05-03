import argparse
import logging
import os
import sys
import yaml

import requests

from itertools import chain
from multiprocessing.pool import ThreadPool
from typing import Dict, NamedTuple
from urllib import parse

from requests_toolbelt.sessions import BaseUrlSession

from var_sync import CLI_NAME

logging.basicConfig(
    stream=sys.stdout,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(CLI_NAME)

Gitlab = NamedTuple('Gitlab', [
    ('url', str), ('token', str)
])

ProjectVariable = NamedTuple('ProjectVariable', [
    ('project', str), ('project_encoded', str),
    ('key', str), ('value', str)
])


VariableResult = NamedTuple('VariableResult', [
    ('variable', ProjectVariable),
    ('success', bool), ('message', str)
])


def get_session(gitlab: Gitlab) -> BaseUrlSession:
    s = BaseUrlSession(base_url=gitlab.url)
    # add the token
    s.headers.update({"PRIVATE-TOKEN": gitlab.token})

    return s


def sync_var(gitlab: Gitlab, variable: ProjectVariable) -> VariableResult:
    try:
        with get_session(gitlab) as s:
            # gitlab doesn't have an UPSERT concept, so we need to know if this variable already exists in the project
            variable_code = s.get(f"/api/v4/projects/{variable.project_encoded}/variables/{variable.key}").status_code

            # variable wasn't found, need to add it
            if variable_code == requests.status_codes.codes.not_found:
                response = s.post(
                    f"/api/v4/projects/{variable.project_encoded}/variables",
                    json={'key': variable.key, 'value': variable.value}
                )
            # update the existing variable
            else:
                response = s.put(
                    f"/api/v4/projects/{variable.project_encoded}/variables/{variable.key}",
                    json={'value': variable.value}
                )

            # variable update should succeed
            response.raise_for_status()
            return VariableResult(
                variable=variable,
                success=True, message=''
            )
    except requests.exceptions.HTTPError as httpError:
        message = httpError.response.text
    except Exception as e:
        message = f"{type(e).__name__}: {str(e)}"

    return VariableResult(
        variable=variable,
        success=False, message=message
    )


def var_sync(args: Dict) -> int:
    # load the projects file
    logger.info(f"Parsing Project Config File: {args['projects']}")
    with open(args['projects'], 'r') as f:
        # the goal here isn't great software, just what works (it's non-production after all),
        # expect the input to be sane
        projects = yaml.safe_load(f)['projects']

    logger.debug("Building Gitlab Connection Object")
    gitlab = Gitlab(url=args['api_url'], token=args['api_token'])

    # do a bulk fetch of the environment vars to make sure we have everything accounted for
    logger.info("Fetching Environment Variables")
    env_vars = {k: os.environ[k] for k in set(chain.from_iterable((v.keys() for v in projects.values())))}

    # build the per-project variable mapping, this will make it so all project/variable mappings will be processed
    # at once
    project_vars = {(
        gitlab,
        ProjectVariable(
            project_encoded=parse.quote_plus(project_path),
            project=project_path,
            key=dest_var,
            value=env_vars[source_var]
        ))
        for project_path, project_vars in projects.items()
        for source_var, dest_var in project_vars.items()
    }

    logger.info(f"Processing {len(project_vars)} Project Variables on {args['max_threads']} Threads")
    with ThreadPool(processes=args['max_threads']) as p:
        results = p.starmap(sync_var, project_vars)

    update_errors = 0
    for result in results:
        if result.success:
            logger.info(f"Updated {result.variable.project} Variable {result.variable.key}")
        else:
            update_errors += 1
            logger.error(
                f"Failed Updating {result.variable.project} Variable {result.variable.key}: {result.message}"
            )

    logger.info(f"Finished {CLI_NAME}")

    return 1 if update_errors else 0


def main():
    script_args = argparse.ArgumentParser()

    script_args.add_argument('--projects', metavar='/path/to/projects.yaml',
                             help='projects and variables to sync', required=True)

    script_args.add_argument('--api-url', metavar='https://gitlab',
                             help='url of the gitlab v4 api endpoint', required=True)
    script_args.add_argument('--api-token', metavar='EzjyTuaBxML9R3cTioST',
                             help='token capable of updating variables in the gitlab projects', required=True)

    script_args.add_argument('--max-threads', metavar='10',
                             help='maximum number of threads to use for gitlab operations', default=10, type=int)

    parsed_script_args = script_args.parse_args()

    logger.info(f"Starting {CLI_NAME}")
    exit(var_sync(vars(parsed_script_args)))
