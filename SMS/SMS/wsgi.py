
import os
from django.core.wsgi import get_wsgi_application

# Local development is selected explicitly by manage.py. A deployed WSGI
# process must never silently fall back to ephemeral local media storage.
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "SMS.settings.prod")
application = get_wsgi_application()
