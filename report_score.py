"""Report protected grader output; learner files are never imported or executed."""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

REPORT_URL = 'https://tensorops.ai/api/github/report'
AUDIENCE = 'https://tensorops.ai/code-labs'


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def deliver(score, token, open_request, sleep=time.sleep):
    for attempt in range(5):
        request = urllib.request.Request(REPORT_URL, data=score, method='POST', headers={
            'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        try:
            with open_request(request, timeout=15) as response:
                status = response.status
        except urllib.error.HTTPError as error:
            status = error.code
            error.close()
        except (urllib.error.URLError, TimeoutError, OSError):
            status = 0
        print('report: HTTP ' + str(status))
        if 200 <= status < 300:
            return
        if status not in (0, 429) and not 500 <= status < 600:
            raise RuntimeError('Score report rejected: HTTP ' + str(status))
        if attempt < 4:
            sleep(2 ** (attempt + 1))
    raise RuntimeError('Score report retries exhausted; artifact recovery remains available')


def main():
    score = Path('result/score.json').read_bytes()
    if not score or len(score) > 65536:
        raise RuntimeError('Invalid protected score size')
    opener = urllib.request.build_opener(NoRedirect())
    url = os.environ['ACTIONS_ID_TOKEN_REQUEST_URL']
    separator = '&' if '?' in url else '?'
    request = urllib.request.Request(url + separator + urllib.parse.urlencode({'audience': AUDIENCE}),
        headers={'Authorization': 'Bearer ' + os.environ['ACTIONS_ID_TOKEN_REQUEST_TOKEN']})
    with opener.open(request, timeout=15) as response:
        raw = response.read(65537)
    if len(raw) > 65536:
        raise RuntimeError('OIDC response too large')
    token = json.loads(raw).get('value')
    if not isinstance(token, str) or not token or len(token) > 16384:
        raise RuntimeError('OIDC response has no valid token')
    print('::add-mask::' + token)
    deliver(score, token, opener.open)


if __name__ == '__main__':
    main()
