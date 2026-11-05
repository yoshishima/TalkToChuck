import getpass
import os

APP_SECRET_ENV_VAR = 'SCHWAB_APP_SECRET'


def resolve_app_secret(app_secret):
    '''Return the app secret from ``--app_secret``, the ``SCHWAB_APP_SECRET``
    environment variable, or an interactive prompt, in that order.

    Secrets passed on the command line are visible in process listings and
    shell history, so the environment variable or the prompt is preferred.
    '''
    if app_secret:
        return app_secret
    app_secret = os.environ.get(APP_SECRET_ENV_VAR)
    if app_secret:
        return app_secret
    return getpass.getpass('App secret: ')
