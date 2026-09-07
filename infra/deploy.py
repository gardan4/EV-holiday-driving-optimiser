"""Apply production infrastructure without advancing container releases or losing secrets."""
import ipaddress
import json
import os
from pathlib import Path
import subprocess
import tempfile
import urllib.request

SUBSCRIPTION = 'cda65360-878b-48f2-a774-6eb4782a95aa'
GROUP = 'evtrip-prod-rg'
SERVER = 'evtrip-sql-prod-se'


def azure(*args, optional=False):
    r = subprocess.run(['az', *args, '--subscription', SUBSCRIPTION, '--only-show-errors', '-o', 'json'],
                       capture_output=True, text=True)
    if r.returncode:
        if optional and ('ResourceNotFound' in r.stderr or 'NotFound' in r.stderr):
            return None
        raise RuntimeError(f'Azure operation failed: {args[:3]}: {r.stderr[:1200]}')
    return json.loads(r.stdout or 'null')


def main():
    actual = json.loads(subprocess.check_output(['az', 'account', 'show', '-o', 'json']))['id']
    if actual != SUBSCRIPTION:
        raise RuntimeError('Refusing to deploy to a different subscription')
    stage = os.environ.get('MIGRATION_STAGE') == 'true'
    if not stage and os.environ.get('MIGRATION_COMPLETE') != 'true':
        raise RuntimeError('Production cutover has not been completed; use migration_stage for staging')
    database = azure('sql', 'db', 'show', '-g', GROUP, '-s', SERVER, '-n', 'evtripdb-prod')
    if database['status'] != 'Online':
        raise RuntimeError('The restored production database must be online before deploying apps')
    values = {'environmentName':'prod', 'location':'northeurope', 'sqlLocation':'swedencentral',
              'sqlServerNameOverride':SERVER, 'migrationStage':stage, 'restrictToCloudflare':not stage,
              'frontendUrl':'https://evtrip.dev', 'apiUrl':'https://api.evtrip.dev',
              'siteUrl':'https://evtrip.dev', 'corsOrigins':'https://evtrip.dev', 'githubOwner':'gardan4'}
    required = {'SQL_ADMIN_PASSWORD':'sqlAdminPassword', 'APP_SECRET_KEY':'secretKey',
                'APP_ENCRYPTION_KEY':'encryptionKey', 'ORS_API_KEY':'orsApiKey', 'OCM_API_KEY':'ocmApiKey'}
    optional = {'GHCR_TOKEN':'ghcrToken', 'FEEDBACK_TOKEN':'feedbackToken', 'STATS_TOKEN':'statsToken',
                'DISCORD_WEBHOOK_URL':'discordWebhookUrl'}
    for key, param in {**required, **optional}.items():
        value = os.environ.get(key, '')
        if key in required and not value:
            raise RuntimeError(f'Required deployment secret is missing: {key}')
        values[param] = value
    if stage:
        runner_ip = str(ipaddress.IPv4Address(urllib.request.urlopen('https://api.ipify.org', timeout=20).read().decode()))
        operators = [str(ipaddress.IPv4Address(ip)) for ip in os.environ.get('MIGRATION_OPERATOR_IPS','').split(',') if ip]
        values['stagingAllowedIps'] = ','.join(sorted(set([runner_ip, *operators])))
    snapshots = {'api':'dev-410722c0784da7c5d31d6f8f484b3b13fc41b37c', 'web':'dev-b8628e320d581ef4005bd42d441cfffde14257c5'}
    for component in ['api', 'web']:
        config = azure('webapp','config','show','-g',GROUP,'-n',f'evtrip-{component}-prod',optional=True)
        if config:
            image = config['linuxFxVersion']
            expected = f'DOCKER|ghcr.io/gardan4/evtrip-{component}:'
            if not image.startswith(expected):
                raise RuntimeError(f'Unexpected image registry for {component}')
            values[f'{component}ImageTag'] = image.removeprefix(expected)
        elif stage:
            values[f'{component}ImageTag'] = snapshots[component]
        else:
            raise RuntimeError('Missing app after production cutover')
    app = azure('webapp','show','-g',GROUP,'-n','evtrip-api-prod',optional=True)
    values['sqlAllowedIps'] = app.get('possibleOutboundIpAddresses', '') if app else ''
    fd, filename = tempfile.mkstemp(suffix='.parameters.json')
    try:
        os.fchmod(fd,0o600)
        with os.fdopen(fd,'w') as f:
            json.dump({'parameters':{k:{'value':v} for k,v in values.items()}},f)
        # Secure parameters are never interpolated into shell commands or printed.
        azure('deployment','group','create','-g',GROUP,'-n','evtrip-'+os.environ.get('GITHUB_RUN_ID','manual'),
              '--template-file',str(Path(__file__).with_name('main.bicep')),'--parameters','@'+filename)
    finally:
        os.unlink(filename)
    app = azure('webapp','show','-g',GROUP,'-n','evtrip-api-prod')
    ips = app['possibleOutboundIpAddresses'].split(',')
    for i, ip in enumerate(ips):
        ipaddress.ip_address(ip)
        azure('sql','server','firewall-rule','create','-g',GROUP,'-s',SERVER,'-n',f'appservice-outbound-{i}',
              '--start-ip-address',ip,'--end-ip-address',ip)
    azure('webapp','restart','-g',GROUP,'-n','evtrip-api-prod')
    print(f'Infrastructure applied in {GROUP}; preserved current images, {len(ips)} database access rules, staging={stage}')


if __name__ == '__main__':
    main()
