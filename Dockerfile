# BlueOS extension: Victron VE.Direct solar charge controller monitor
FROM python:3.12-slim-bookworm

WORKDIR /app

ENV PYTHONUNBUFFERED=1
ENV DATA_DIR=/data

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static

EXPOSE 80

LABEL version="1.0.0"
LABEL type="device-integration"
LABEL requirements="core >= 1.1"
LABEL tags='["data-collection", "power", "solar", "sensor"]'

LABEL org.blueos.version="1.0.0"
LABEL org.blueos.type="device-integration"
LABEL org.blueos.requirements="core >= 1.1"
LABEL org.blueos.tags='["data-collection", "power", "solar", "sensor"]'

# Privileged + /dev bind: required to open the USB serial adapter (/dev/ttyUSB0)
# the VE.Direct cable presents. /data persists the CSV log and settings.json.
LABEL permissions='{\
  "ExposedPorts": {\
    "80/tcp": {}\
  },\
  "HostConfig": {\
    "Privileged": true,\
    "ExtraHosts": ["host.docker.internal:host-gateway"],\
    "PortBindings": {\
      "80/tcp": [{\
        "HostPort": ""\
      }]\
    },\
    "Binds": [\
      "/usr/blueos/extensions/vedirect:/data",\
      "/dev:/dev"\
    ]\
  }\
}'
LABEL org.blueos.permissions='{\
  "ExposedPorts": {\
    "80/tcp": {}\
  },\
  "HostConfig": {\
    "Privileged": true,\
    "ExtraHosts": ["host.docker.internal:host-gateway"],\
    "PortBindings": {\
      "80/tcp": [{\
        "HostPort": ""\
      }]\
    },\
    "Binds": [\
      "/usr/blueos/extensions/vedirect:/data",\
      "/dev:/dev"\
    ]\
  }\
}'

LABEL authors='[{"name": "Tony White", "email": "tony@bluerobotics.com"}]'
LABEL org.blueos.authors='[{"name": "Tony White", "email": "tony@bluerobotics.com"}]'

LABEL company='{\
    "about": "Victron VE.Direct solar charge controller logging and MAVLink telemetry",\
    "name": "Community",\
    "email": "support@bluerobotics.com"\
}'
LABEL org.blueos.company='{\
    "about": "Victron VE.Direct solar charge controller logging and MAVLink telemetry",\
    "name": "Community",\
    "email": "support@bluerobotics.com"\
}'

LABEL readme='https://raw.githubusercontent.com/vshie/VEdirect/{tag}/README.md'
LABEL org.blueos.readme='https://raw.githubusercontent.com/vshie/VEdirect/{tag}/README.md'

LABEL links='{\
    "source": "https://github.com/vshie/VEdirect"\
}'
LABEL org.blueos.links='{\
    "source": "https://github.com/vshie/VEdirect"\
}'

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "80"]
