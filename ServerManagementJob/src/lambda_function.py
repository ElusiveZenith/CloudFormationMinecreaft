import os
import time
import boto3
import requests
client = boto3.client('ecs')
logs_client = boto3.client('logs')
route_client = boto3.client('route53')
scheduler_client = boto3.client('scheduler')

cluster = os.environ.get('CLUSTER')
service = os.environ.get('SERVICE')
container = os.environ.get('CONTAINER')
dns_name = os.environ.get('DNS_NAME')
hosted_zone_id = os.environ.get('HOSTED_ZONE_ID')
scheduler_name = os.environ.get('SCHEDULER_NAME')
discord_webhook_url = os.environ.get('DISCORD_WEBHOOK_URL') or None
discord_comment_username = os.environ.get('DISCORD_WEBHOOK_USERNAME') or 'Minecraft Server'
discord_error_admin_id = os.environ.get('DISCORD_ERROR_ADMIN_ID') or None


def post_discord_message(message, admin_ping=False):
  if discord_webhook_url is None:
    return
  if admin_ping and discord_error_admin_id:
    message = f'<{discord_error_admin_id}> {message}'
  data = {
    "content": message,
    "username": discord_comment_username
  }
  requests.post(discord_webhook_url, json=data)


def cron_job_state(enabled):
  response = scheduler_client.get_schedule(Name=scheduler_name)
  response['State'] = "ENABLED" if enabled else "DISABLED"
  response.pop('Arn', None)
  response.pop('CreationDate', None)
  response.pop('LastModificationDate', None)
  response.pop('ResponseMetadata', None)
  scheduler_client.update_schedule(**response)


def stop_server():
  print("Stopping Server")
  deregister_ip()
  client.update_service(
      cluster=cluster,
      service=service,
      desiredCount=0
  )
  cron_job_state(False)
  post_discord_message("Server Shutting Down")


def get_ssm_session(sessionId):
  ssm_client = boto3.client('ssm')
  session_response = ssm_client.describe_sessions(
    State='History',
    Filters=[{
      'key': 'SessionId',
      'value': sessionId
    }]
  )
  return session_response['Sessions']


def is_server_running():
  try:
    task_arn = client.list_tasks(cluster=cluster).get('taskArns')[0]
    response = client.describe_tasks(
        cluster=cluster,
        tasks=[task_arn],
    )
    return response['tasks'][0]['containers'][0]['lastStatus'] == 'RUNNING'
  except Exception:
    return False
  

def get_players(task_arn):
  print('Get player count - START')
  exec_resp = client.execute_command(
    cluster=cluster,
    container=container,
    command="bash -c 'netstat -atn | grep :25565 | grep ESTABLISHED | wc -l'",
    interactive=True,
    task=task_arn
  )
  session = get_ssm_session(exec_resp['session']['sessionId'])
  # Paginate until Session completes and logs uploaded to CloudWatch logs
  while (session == None or len(session) == 0):
    time.sleep(3)
    session = get_ssm_session(exec_resp['session']['sessionId'])

  time.sleep(3)
  response = logs_client.get_log_events(
    logGroupName='Minecraft',
    logStreamName=exec_resp['session']['sessionId'],
    startFromHead=True
  )
  player_count = int(response['events'][0]['message'].split('\n')[1].replace('\r', ''))
  print(f"Number of active players: {player_count}")
  print('Get player count - END')
  return int(response['events'][0]['message'].split('\n')[1].replace('\r', ''))


def set_ip_to_dns(ip):
  print(f'Setting IP to {ip}')
  all_records = route_client.list_resource_record_sets(HostedZoneId=hosted_zone_id).get('ResourceRecordSets')
  server_record = [record for record in all_records if record['Name'] == f"{dns_name}."][0] or None # None isn't going to work because it modified that return
  server_record['ResourceRecords'] = [{'Value': ip}]
  return route_client.change_resource_record_sets(
    HostedZoneId=hosted_zone_id,
    ChangeBatch={
      'Changes': [{
        'Action': 'UPSERT',
        'ResourceRecordSet': server_record
      }]
    }
  )


def register_ip():
  print('Register IP - START')
  # Get public ip of server
  task_arns = client.list_tasks(cluster=cluster).get('taskArns')
  if len(task_arns) == 0:
    print("Server not running - Can't register IP")
    return
  attachments = [task.get("attachments") for task in client.describe_tasks(cluster=cluster, tasks=[task_arns[0]]).get('tasks')][0]
  details = [attachment.get("details") for attachment in attachments][0]
  eni = [detail for detail in details if detail.get("name") == "networkInterfaceId"][0].get('value')
  eni_resource = boto3.resource("ec2").NetworkInterface(eni)
  public_ip = eni_resource.association_attribute.get("PublicIp")
  print(f'Server Public IP: {public_ip}')

  response = set_ip_to_dns(public_ip)
  if response.get('ResponseMetadata', {}).get('HTTPStatusCode', 500) != 200:
    post_discord_message(f'Error setting DNS. Connect using: {public_ip}',  admin_ping=True)
    print('ERROR: Unable to set Route53 record')
  else:
    print('Adding DNS tag')
    client.tag_resource(
      resourceArn=service,
      tags=[{
        'key': 'DNS',
        'value': 'true',
      }],
    )
    post_discord_message('Server Started')
  print('Register IP - END')


def deregister_ip():
  print('Deregister IP - START')
  response = set_ip_to_dns('0.0.0.0')
  if response.get('ResponseMetadata', {}).get('HTTPStatusCode', 500) != 200:
    print('ERROR: Unable to set Route53 record', response.get('message'))
  client.untag_resource(resourceArn=service, tagKeys=['DNS'])
  print('Deregister IP - END')


def check_dns():
  tags = client.list_tags_for_resource(resourceArn=service).get('tags')
  if 'DNS' not in [tag['key'] for tag in tags] and is_server_running():
    register_ip()


def has_active_players():
  try:
      client.untag_resource(resourceArn=service, tagKeys=['NoPlayerChecks'])
  except Exception:
      pass
  # TODO: If uptime is more than x, send AFK reminder to discord / notification to aws admin
  # TODO: If uptime is more than y, restart server after warning


def no_active_players():
  print('No active players - START')
  tags = client.list_tags_for_resource(resourceArn=service).get('tags')
  player_check_tags = [tag for tag in tags if tag['key'] == 'NoPlayerChecks']
  player_check_tag = player_check_tags[0] if len(player_check_tags) > 0 else None
  check_val = int(player_check_tag['value']) if player_check_tag else 0
  if check_val >= 2:
    print(f'Server has been inactive through {check_val} checks. Shutting Down.')
    client.untag_resource(resourceArn=service, tagKeys=['NoPlayerChecks'])
    deregister_ip()
    stop_server()
  else:
    print(f'NoPlayerChecks: {check_val+1}')
    client.tag_resource(
      resourceArn=service,
      tags=[{
          'key': 'NoPlayerChecks',
          'value': str(check_val+1),
      }],
    )		
  print('No active players - END')


def check_player_count():
  try:
    task_arns = client.list_tasks(cluster=cluster).get('taskArns')
    if len(task_arns) == 0:
      print("Server not running - Not checking player count")
    elif get_players(task_arns[0]) > 0:
      has_active_players()
    else:
      no_active_players()
  except client.exceptions.InvalidParameterException:
    print("Server is launching - Not checking player count")


def lambda_handler(event=None, context=None):
  try:
    check_dns()
    check_player_count()
  except Exception as e:
    post_discord_message('An error occurred, check logs for more details.', admin_ping=True)
    raise e
