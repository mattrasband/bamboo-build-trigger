#!/usr/bin/env python3
import asyncio
from functools import wraps
import logging
import os
import time

import aiohttp
from aiohttp import web
from marshmallow import Schema, fields
import simplejson as json
import uvloop

logging.basicConfig(level=logging.DEBUG,
                    format='%(asctime)s %(levelname)-8s %(message)s')

asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())
loop = asyncio.get_event_loop()


app = web.Application(loop=loop)
app.update(BAMBOO_URL=os.environ['BAMBOO_URL'],
           BAMBOO_USERNAME=os.environ['BAMBOO_USERNAME'],
           BAMBOO_PASSWORD=os.environ['BAMBOO_PASSWORD'],
           MAX_RETRIES=int(os.getenv('MAX_RETRIES', 6)),
           RETRY_INTERVAL=int(os.getenv('RETRY_INTERVAL', 10)))


def resolve_request(*args, **kwargs):
    """Resolve the request from the arguments to a function/method"""
    arg0 = args[0]
    if isinstance(arg0, web.Request):
        return arg0
    if isinstance(arg0, web.abc.AbstractView):
        return arg0.request
    raise ValueError('Unable to resolve request from {!r}'.format(arg0))


def api_error_handler(f):
    """Wraps an API method in an error handler to ensure it returns
    a JSON response.
    """
    @wraps(f)
    async def wrapper(*args, **kwargs):
        try:
            return await f(*args, **kwargs)
        except json.JSONDecodeError as e:
            logging.error('Unable to decode JSON: %s', e)
            return web.json_response({
                'error': 'Unable to decode JSON',
            }, status=400)
    return wrapper


async def wait_for_deploy(url, git_sha, retries=6, interval=10):
    """Waits to validate the deployment based on getting the SHA from
    the provided url.

    Note that we will time out if the deploy has not been confirmed before
    the (retries * interval) seconds has elapsed.

    :param url: URL that returns JSON with a 'sha' parameter to validate
    :param git_sha: The expected sha for the new deployment
    :param retries: Number of retries to allow
    :param interval: Interval to try on, in seconds.
    :returns bool: True if it came up, False otherwise.
    """
    start = time.time()
    while (time.time() - start) <= (retries * interval):
        logging.debug('Waiting %ss before checking', interval)
        await asyncio.sleep(interval)

        async with aiohttp.get(url, headers={'Accept': 'application/json'}) as resp:
            if resp.status != 200:
                logging.debug('Bad Response, trying again in a few')
                continue
            logging.debug('Response okay, checking sha...')
            try:
                js = await resp.json()
                sha = js.get('app', {}).get('git_sha')
                if not sha:
                    logging.debug('GIT SHA not found in the response')
                    continue
                if sha == git_sha:
                    logging.debug('SHA Matched')
                    return True
                logging.debug('GIT SHA not expected, retrying...')
            except:
                pass
    logging.debug('Task expired, deployment not found')
    return False


async def trigger_build(js, bamboo_url, credentials=None):
    build_url = bamboo_url + '/rest/api/latest/queue/{plan_key}-{build_number}'
    build_url = build_url.format(plan_key=js['plan_key'],
                                 build_number=js['build_number'])
    print('Calling to "{}"'.format(build_url))
    params = {
        'headers': {
            'Accept': 'application/json',
            'Content-Type': 'application/json',
        },
    }

    if credentials:
        params['auth'] = aiohttp.BasicAuth(credentials[0], credentials[1])

    async with aiohttp.put(build_url, **params) as resp:
        if resp.status == 400:
            logging.error('Next stage cannot be resumed')
            return
        if resp.status == 200:
            logging.info('Build resumed')


async def poll_for_up(js, bamboo_url, credentials=None, retries=6, interval=10):
    logging.info('Waiting for service %s to boot', js)
    deployed = await wait_for_deploy(js['info_url'], js['git_sha'], retries=retries, interval=interval)
    if not deployed:
        logging.info('Timed out waiting for the deploy')
        return

    logging.info('Deploy confirmed, triggering next phase')
    await trigger_build(js, bamboo_url, credentials=credentials)


class BuildWatcherSchema(Schema):
    info_url = fields.Url(required=True)
    git_sha = fields.String(required=True)
    plan_key = fields.String(required=True)
    build_number = fields.Integer(required=True)


def consumes(schema, *, err_status=400):
    """Loads and validates request data via the schema based on the
    request type:

        * GET: Loaded from the query parameters
        * POST/PUT/PATCH: Loaded from the POST data (json if application/json)
    """
    def decor(f):
        @wraps(f)
        async def wrapper(*args, **kwargs):
            request = resolve_request(*args, **kwargs)
            if request.method in ('GET',):
                to_load = request.GET
            elif request.method in ('PUT', 'POST', 'PATCH'):
                if 'application/json' in request.headers.get('Content-Type', ''):
                    to_load = await request.json(loads=json.loads)
                else:
                    to_load = await request.post()

            many = isinstance(to_load, list)
            payload, err = schema().load(to_load, many=many)
            if err:
                return web.json_response(err, status=err_status)
            request['payload'] = payload
            return await f(*args, **kwargs)
        return wrapper
    return decor


@consumes(BuildWatcherSchema)
@api_error_handler
async def watcher_handler(request):
    credentials = (request.app['BAMBOO_USERNAME'],
                   request.app['BAMBOO_PASSWORD'])
    task = poll_for_up(request['payload'], request.app['BAMBOO_URL'],
                       credentials=credentials,
                       retries=request.app['MAX_RETRIES'],
                       interval=request.app['RETRY_INTERVAL'])
    request.app.loop.create_task(task)
    return web.json_response({})


app.router.add_route('POST', '/api/watch', watcher_handler)


if __name__ == '__main__':
    web.run_app(app, host='0.0.0.0', port=8080)
