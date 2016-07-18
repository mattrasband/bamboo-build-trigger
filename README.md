# bamboo-build-trigger

**POC: Just a quick prototype for solving a slow deployment pipeline problem for dependent build steps to be triggered after an async deployment plan reports a "success".**

This is a quick server that handles triggering (starting the next phase) of bamboo builds after waiting for a service to "come up" (based on the git SHA1 on an `/info` route).

## Use:

    pip install -Ur requirements.txt
    export BAMBOO_URL=http://<your bamboo url>
    export BAMBOO_USERNAME=<username that can do stuff>
    export BAMBOO_PASSWORD=<password that can do stuff>
    python server.py


Then POST to `/api/watch` with:

    {
        "info_url": <url to the target server's info route, needs to report `git_sha`>,
        "git_sha": <whatever sha to look for>,
        "plan_key": <bamboo plan key ${bamboo_planKey}>,
        "build_number": <bamboo build number ${bamboo_buildNumber}>,
    }

There is no real failure reporting beyond console... Like I said, POC.
