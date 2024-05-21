import boto3
import os

client = boto3.client('ecs')

def update_service_desired_count(desired_count):
  client.update_service(
      cluster=os.environ.get('CLUSTER') or 'Minecraft-ServerCluster',
      service=os.environ.get('SERVICE') or 'Minecraft',
      desiredCount=desired_count
  )


def start_server():
  print("Starting Server")
  update_service_desired_count(1)
  # Start cron job
  # Post to discord


def lambda_handler(event, context):
  match event['state']:
    case "start":
      return start_server()
