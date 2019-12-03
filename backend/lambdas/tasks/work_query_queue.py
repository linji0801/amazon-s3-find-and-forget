import json
import os
import boto3

from decorators import with_logger
from boto_utils import read_queue

concurrency_limit = os.getenv("AthenaConcurrencyLimit", 20)
queue_url = os.getenv("QueueUrl")
state_machine_arn = os.getenv("StateMachineArn")
sqs = boto3.resource("sqs")
sf_client = boto3.client("stepfunctions")


@with_logger
def handler(event, context):
    execution_id = event["ExecutionId"]
    job_id = event["ExecutionName"]
    queue = sqs.Queue(queue_url)
    not_visible = int(event["QueryQueue"]["NotVisible"])
    visible = int(event["QueryQueue"]["Visible"])
    limit = int(concurrency_limit)
    reamining_capacity = limit - not_visible
    to_process = min(reamining_capacity, visible)
    if to_process > 0:
        msgs = read_queue(queue, to_process)
        for msg in msgs:
            context.logger.debug(msg.body)
            # TODO: Handle message received multiple times
            body = json.loads(msg.body)
            body["AWS_STEP_FUNCTIONS_STARTED_BY_EXECUTION_ID"] = execution_id
            body["JobId"] = job_id
            body["ReceiptHandle"] = msg.receipt_handle
            sf_client.start_execution(stateMachineArn=state_machine_arn, input=json.dumps(body))
