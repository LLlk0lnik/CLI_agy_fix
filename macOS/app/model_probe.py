"""Explicit, small model probes; responses and credentials never enter reports."""
import json
import re
import urllib.error
from route_health import NoAvailableRoute, classify_error, has_model_answer


def probe_name(model, catalog):
    base = re.sub(r'-(?:high|medium|low|max|minimal)$', '', model)
    if base != model and (base in catalog or base + '-tiered' in catalog):
        return base
    return model


def probe_model(backend, model):
    try:
        if backend.catalog is None:
            backend.catalog = backend.models().get('models', {})
        name = probe_name(model, backend.catalog)
        # No tools, no workspace content; only this fixed synthetic prompt.
        body = {'contents': [{'role': 'user', 'parts': [{'text': 'Reply with OK.'}]}],
                'generationConfig': {'maxOutputTokens': 256}}
        with backend.generate(name, body, True) as response:
            total = 0
            for line in response:
                total += len(line)
                if total > 1024 * 1024:
                    break
                if not line.startswith(b'data:'):
                    continue
                try:
                    value = json.loads(line[5:].strip())
                except (ValueError, UnicodeError):
                    continue
                if has_model_answer(value):
                    route = getattr(response, 'agycli_route', None)
                    backend.routes.answer(route)
                    return {'model': model, 'status': 'model_answer', 'proxy': route}
        return {'model': model, 'status': 'no_model_answer'}
    except NoAvailableRoute:
        return {'model': model, 'status': 'no_available_route'}
    except urllib.error.HTTPError as error:
        category = classify_error(error.code, error.read())
        code = error.code
        error.close()
        return {'model': model, 'status': category, 'http_status': code}
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        # Fixed class name only: str(error) could embed sensitive backend responses.
        return {'model': model, 'status': 'connection_or_profile_error', 'error_type': type(error).__name__}
