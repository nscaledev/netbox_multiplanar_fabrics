ARG NETBOX_IMAGE=netboxcommunity/netbox:v4.2.3-3.2.0

FROM ${NETBOX_IMAGE}

USER root

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates postgresql-client \
    && rm -rf /var/lib/apt/lists/*

COPY plugin_requirements.txt /opt/netbox/plugin_requirements.txt
COPY configuration/plugins.py /etc/netbox/config/plugins.py
COPY configuration/extra.py /etc/netbox/config/extra.py
COPY vendor/netbox-plugin-prometheus-sd /opt/netbox/local-plugins/netbox-plugin-prometheus-sd
COPY vendor/netbox_multiplanar_fabrics /opt/netbox/local-plugins/netbox_multiplanar_fabrics
COPY vendor/netbox_power_plant /opt/netbox/local-plugins/netbox_power_plant

RUN /usr/local/bin/uv pip install -r /opt/netbox/plugin_requirements.txt \
    && /usr/local/bin/uv pip install /opt/netbox/local-plugins/netbox-plugin-prometheus-sd \
    && SETUPTOOLS_SCM_PRETEND_VERSION_FOR_NETBOX_PLANT_GRAPH=0.1.0 \
       SETUPTOOLS_SCM_PRETEND_VERSION_FOR_NETBOX_POWER_PLANT=0.1.0 \
       /usr/local/bin/uv pip install --no-deps \
       -e /opt/netbox/local-plugins/netbox_multiplanar_fabrics \
       -e /opt/netbox/local-plugins/netbox_power_plant

RUN DEBUG="false" \
    SECRET_KEY="dummydummydummydummydummydummydummydummydummydummy" \
    /opt/netbox/venv/bin/python /opt/netbox/netbox/manage.py collectstatic --no-input
