
import os
from django.core.asgi import get_asgi_application

# Match the WSGI deployment default; manage.py remains the local-dev entrypoint.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "SMS.settings.prod")
application = get_asgi_application()
