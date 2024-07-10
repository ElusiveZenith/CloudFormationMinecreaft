import os
import boto3
import requests

client = boto3.client('ecs')
scheduler_client = boto3.client('scheduler')

discord_webhook_url = os.environ.get('DISCORD_WEBHOOK_URL') or None
discord_comment_username = os.environ.get('DISCORD_WEBHOOK_USERNAME') or 'Minecraft Server'
discord_error_admin_id = os.environ.get('DISCORD_ERROR_ADMIN_ID') or None
discord_notification_role_id = os.environ.get('DISCORD_NOTIFICATION_ROLE_ID') or None


def post_discord_message(message, notification_ping=False, admin_ping=False):
  if discord_webhook_url is None:
    return
  if notification_ping and discord_notification_role_id:
    message = f'<{discord_notification_role_id}> {message}'
  if admin_ping and discord_error_admin_id:
    message = f'<{discord_error_admin_id}> {message}'
  data = {
      "content": message,
      "username": discord_comment_username
  }
  requests.post(discord_webhook_url, json=data)


def update_service_desired_count(desired_count):
  client.update_service(
    cluster=os.environ.get('CLUSTER') or 'Minecraft-ServerCluster',
    service=os.environ.get('SERVICE') or 'Minecraft',
    desiredCount=desired_count
  )


def cron_job_state(enabled):
  response = scheduler_client.get_schedule(
      Name='Minecraft-Server-Monitor-Schedule'
  )
  response['State'] = "ENABLED" if enabled else "DISABLED"
  response.pop('Arn', None)
  response.pop('CreationDate', None)
  response.pop('LastModificationDate', None)
  response.pop('ResponseMetadata', None)
  scheduler_client.update_schedule(**response)


def start_server():
  print("Starting Server")
  try:
    cron_job_state(True)
  except Exception as e:
    post_discord_message("Unable to start server. Failed to start server manager.", admin_ping=True)
    raise e
  update_service_desired_count(1)
  post_discord_message("Launching Server", notification_ping=True)
  return 200, "Server Launch Initiated"


def routing(path):
  match path:
    case "/start":
      return start_server()


def lambda_handler(event, context):
  status_code, message = routing(event['path'])
  return {
      "statusCode": status_code,
      "headers": {
          "Content-Type": "*/*"
      },
      "body": message
  }
