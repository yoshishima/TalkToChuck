#!/usr/bin/env python
import argparse
import sys

import schwab
from schwab.scripts import APP_SECRET_ENV_VAR, resolve_app_secret


def main(api_key, app_secret, callback_url, token_path, requested_browser):
    try:
        schwab.auth.client_from_login_flow(
                api_key, app_secret, callback_url, token_path,
                requested_browser=requested_browser, callback_timeout=300)
        return 0
    except Exception as e:
        # Report why the browser flow failed. These errors describe the
        # callback server, browser or token file; they do not contain tokens.
        print('Failed to fetch a token using a web browser ({}: {}), falling '
              'back to the manual flow'.format(type(e).__name__, e))

    schwab.auth.client_from_manual_flow(api_key, app_secret, callback_url,
                                        token_path)

    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(
            description='Fetch a new token and write it to a file')

    required = parser.add_argument_group('required arguments')
    required.add_argument(
            '--token_file', required=True,
            help='Path to token file. Any existing file will be overwritten')
    required.add_argument('--api_key', required=True)
    required.add_argument('--callback_url', required=True, type=str)
    parser.add_argument(
            '--app_secret',
            help='App secret. Prefer the {} environment variable or the '
                 'interactive prompt: command-line arguments are visible in '
                 'process listings and shell history.'.format(
                     APP_SECRET_ENV_VAR))
    parser.add_argument(
            '--browser', required=False, type=str,
            help='Manually specify a browser in which to start the login '+
            'flow. See here for available options: '+
            'https://docs.python.org/3/library/webbrowser.html#webbrowser.register')

    args = parser.parse_args()

    sys.exit(main(args.api_key, resolve_app_secret(args.app_secret),
                  args.callback_url, args.token_file, args.browser))
