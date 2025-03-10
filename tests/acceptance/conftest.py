import datetime
import json
import logging
import tempfile
from copy import deepcopy
from os import getenv
from pathlib import Path
from urllib.parse import urljoin
from uuid import uuid4

import boto3
import pytest
from botocore.exceptions import ClientError
from botocore.waiter import WaiterModel, create_waiter_with_client
from requests import Session

from . import empty_table
from backend.ecs_tasks.delete_files.cse import encrypt

logger = logging.getLogger()


#########
# HOOKS #
#########


def pytest_configure(config):
    """
    Initial test env setup
    """
    pass


def pytest_unconfigure(config):
    """
    Teardown actions
    """
    pass


############
# FIXTURES #
############


@pytest.fixture(scope="session")
def stack():
    cloudformation = boto3.resource("cloudformation")
    stack = cloudformation.Stack(getenv("StackName", "S3F2"))
    return {o["OutputKey"]: o["OutputValue"] for o in stack.outputs}


@pytest.fixture
def config(stack):
    ssm = boto3.client("ssm")
    return json.loads(
        ssm.get_parameter(Name=stack["ConfigParameter"], WithDecryption=True)[
            "Parameter"
        ]["Value"]
    )


@pytest.fixture
def config_mutator(config, ssm_client, stack):
    ssm = boto3.client("ssm")

    def mutator(**kwargs):
        tmp = {**config, **kwargs}
        ssm.put_parameter(
            Name=stack["ConfigParameter"],
            Value=json.dumps(tmp),
            Type="String",
            Overwrite=True,
        )

    yield mutator
    ssm.put_parameter(
        Name=stack["ConfigParameter"],
        Value=json.dumps(config),
        Type="String",
        Overwrite=True,
    )


@pytest.fixture(scope="session")
def ddb_resource():
    return boto3.resource("dynamodb")


@pytest.fixture(scope="session")
def ddb_client():
    return boto3.client("dynamodb")


@pytest.fixture(scope="session")
def s3_resource():
    return boto3.resource("s3")


@pytest.fixture(scope="session")
def sf_client():
    return boto3.client("stepfunctions")


@pytest.fixture(scope="session")
def glue_client():
    return boto3.client("glue")


@pytest.fixture(scope="session")
def kms_client():
    return boto3.client("kms")


@pytest.fixture(scope="session")
def ssm_client():
    return boto3.client("ssm")


@pytest.fixture(scope="session")
def iam_client():
    return boto3.client("iam")


@pytest.fixture(scope="session")
def glue_columns():
    return [
        {"Name": "customer_id", "Type": "string"},
        {"Name": "customerId", "Type": "int"},
        {
            "Name": "user_info",
            "Type": "struct<personal_information:struct<email:string,first_name:string,last_name:string>>",
        },
        {"Name": "days_off", "Type": "array<string>"},
    ]


@pytest.fixture(scope="session", autouse=True)
def cognito_token(stack):
    # Generate User in Cognito
    user_pool_id = stack["CognitoUserPoolId"]
    client_id = stack["CognitoUserPoolClientId"]
    username = "aws-uk-sa-builders@amazon.com"
    pwd = "!Acceptance1Tests2password!"
    auth_data = {"USERNAME": username, "PASSWORD": pwd}
    provider_client = boto3.client("cognito-idp")
    # Create the User
    provider_client.admin_create_user(
        UserPoolId=user_pool_id,
        Username=username,
        TemporaryPassword=pwd,
        MessageAction="SUPPRESS",
    )
    provider_client.admin_set_user_password(
        UserPoolId=user_pool_id, Username=username, Password=pwd, Permanent=True
    )
    # Allow admin login
    provider_client.update_user_pool_client(
        UserPoolId=user_pool_id,
        ClientId=client_id,
        ExplicitAuthFlows=["ADMIN_NO_SRP_AUTH",],
    )
    # Get JWT token for the dummy user
    resp = provider_client.admin_initiate_auth(
        UserPoolId=user_pool_id,
        AuthFlow="ADMIN_NO_SRP_AUTH",
        AuthParameters=auth_data,
        ClientId=client_id,
    )
    yield resp["AuthenticationResult"]["IdToken"]
    provider_client.admin_delete_user(UserPoolId=user_pool_id, Username=username)


@pytest.fixture(scope="session")
def api_client(cognito_token, stack):
    class ApiGwSession(Session):
        def __init__(self, base_url=None, default_headers=None):
            if default_headers is None:
                default_headers = {}
            self.base_url = base_url
            self.default_headers = default_headers
            super(ApiGwSession, self).__init__()

        def request(
            self, method, url, data=None, params=None, headers=None, *args, **kwargs
        ):
            url = urljoin("{}/v1/".format(self.base_url), url)
            merged_headers = deepcopy(self.default_headers)
            if isinstance(headers, dict):
                merged_headers.update(headers)
            return super(ApiGwSession, self).request(
                method, url, data, params, headers=merged_headers, *args, **kwargs
            )

    hds = {"Content-Type": "application/json"}
    if cognito_token:
        hds.update({"Authorization": "Bearer {}".format(cognito_token)})

    return ApiGwSession(stack["ApiUrl"], hds)


@pytest.fixture(scope="module")
def queue_base_endpoint():
    return "queue"


@pytest.fixture(scope="module")
def settings_base_endpoint():
    return "settings"


@pytest.fixture(scope="module")
def queue_table(ddb_resource, stack):
    return ddb_resource.Table(stack["DeletionQueueTable"])


@pytest.fixture
def del_queue_factory(queue_table):
    def factory(
        match_id="testId",
        deletion_queue_item_id="id123",
        created_at=round(datetime.datetime.now(datetime.timezone.utc).timestamp()),
        data_mappers=[],
        matchid_type="Simple",
    ):
        item = {
            "DeletionQueueItemId": deletion_queue_item_id,
            "MatchId": match_id,
            "CreatedAt": created_at,
            "DataMappers": data_mappers,
        }
        if matchid_type:
            item["Type"] = matchid_type
        queue_table.put_item(Item=item)
        return item

    yield factory

    empty_table(queue_table, "DeletionQueueItemId")


@pytest.fixture(scope="module")
def data_mapper_base_endpoint():
    return "data_mappers"


@pytest.fixture(scope="module")
def data_mapper_table(ddb_resource, stack):
    return ddb_resource.Table(stack["DataMapperTable"])


@pytest.fixture(scope="function")
def empty_data_mappers(data_mapper_table):
    empty_table(data_mapper_table, "DataMapperId")
    yield
    empty_table(data_mapper_table, "DataMapperId")


@pytest.fixture
def glue_table_factory(dummy_lake, glue_client, glue_columns):
    items = []
    bucket_name = dummy_lake["bucket_name"]

    def factory(
        columns=glue_columns,
        fmt="parquet",
        database="acceptancetests",
        table="acceptancetests",
        prefix="prefix",
        partition_keys=[],
        partitions=[],
        partition_key_types="string",
        encrypted=False,
    ):
        glue_client.create_database(DatabaseInput={"Name": database})
        input_format = (
            "org.apache.hadoop.mapred.TextInputFormat"
            if fmt == "json"
            else "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
        )
        output_format = (
            "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"
            if fmt == "json"
            else "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"
        )
        ser_library = (
            "org.openx.data.jsonserde.JsonSerDe"
            if fmt == "json"
            else "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
        )
        glue_client.create_table(
            DatabaseName=database,
            TableInput={
                "Name": table,
                "StorageDescriptor": {
                    "Columns": columns,
                    "Location": "s3://{bucket}/{prefix}/".format(
                        bucket=bucket_name, prefix=prefix
                    ),
                    "InputFormat": input_format,
                    "OutputFormat": output_format,
                    "Compressed": False,
                    "SerdeInfo": {
                        "SerializationLibrary": ser_library,
                        "Parameters": {"serialization.format": "1"},
                    },
                    "StoredAsSubDirectories": False,
                },
                "PartitionKeys": [
                    {"Name": pk, "Type": partition_key_types} for pk in partition_keys
                ],
                "Parameters": {
                    "EXTERNAL": "TRUE",
                    "has_encrypted_data": str(encrypted).lower(),
                },
            },
        )

        for p in partitions:
            glue_client.create_partition(
                DatabaseName=database,
                TableName=table,
                PartitionInput={
                    "Values": p,
                    "StorageDescriptor": {
                        "Columns": columns,
                        "Location": "s3://{bucket}/{prefix}/{parts}/".format(
                            bucket=dummy_lake["bucket_name"],
                            prefix=prefix,
                            parts="/".join(p),
                        ),
                        "InputFormat": input_format,
                        "OutputFormat": output_format,
                        "SerdeInfo": {
                            "SerializationLibrary": ser_library,
                            "Parameters": {"serialization.format": "1"},
                        },
                    },
                },
            )
        item = {"Database": database, "Table": table}
        items.append(item)
        return item

    yield factory

    for i in items:
        db_name = i["Database"]
        table_name = i["Table"]
        glue_client.delete_table(DatabaseName=db_name, Name=table_name)
        glue_client.delete_database(Name=db_name)


@pytest.fixture
def glue_data_mapper_factory(
    glue_client, data_mapper_table, glue_table_factory, glue_columns
):
    """
    Factory for registering a data mapper in DDB and createing a corresponding glue table
    """
    items = []

    def factory(
        data_mapper_id="test",
        columns=glue_columns,
        fmt="parquet",
        database="acceptancetests",
        table="acceptancetests",
        partition_keys=[],
        partitions=[],
        role_arn=None,
        delete_old_versions=False,
        column_identifiers=["customer_id"],
        partition_key_types="string",
        encrypted=False,
    ):
        item = {
            "DataMapperId": data_mapper_id,
            "Columns": column_identifiers,
            "QueryExecutor": "athena",
            "QueryExecutorParameters": {
                "DataCatalogProvider": "glue",
                "Database": database,
                "Table": table,
            },
            "Format": fmt,
            "DeleteOldVersions": delete_old_versions,
        }
        if role_arn:
            item["RoleArn"] = role_arn
        data_mapper_table.put_item(Item=item)
        glue_table_factory(
            prefix=data_mapper_id,
            columns=columns,
            fmt=fmt,
            database=database,
            table=table,
            partition_keys=partition_keys,
            partitions=partitions,
            partition_key_types=partition_key_types,
            encrypted=encrypted,
        )

        items.append(item)
        return item

    yield factory

    empty_table(data_mapper_table, "DataMapperId")


@pytest.fixture(scope="module")
def jobs_endpoint():
    return "jobs"


@pytest.fixture(scope="module")
def job_table(ddb_resource, stack):
    return ddb_resource.Table(stack["JobTable"])


@pytest.fixture(scope="module")
def empty_jobs(job_table):
    empty_table(job_table, "Id", "Sk")
    yield
    empty_table(job_table, "Id", "Sk")


@pytest.fixture
def job_factory(job_table, sf_client, stack):
    items = []

    def factory(
        job_id=str(uuid4()),
        status="QUEUED",
        gsib="0",
        created_at=round(datetime.datetime.now().timestamp()),
        del_queue_items=[],
        **kwargs
    ):
        item = {
            "Id": job_id,
            "Sk": job_id,
            "Type": "Job",
            "JobStatus": status,
            "CreatedAt": created_at,
            "GSIBucket": gsib,
            "AthenaConcurrencyLimit": 15,
            "DeletionTasksMaxNumber": 1,
            "QueryExecutionWaitSeconds": 1,
            "QueryQueueWaitSeconds": 1,
            "ForgetQueueWaitSeconds": 5,
            **kwargs,
        }
        job_table.put_item(Item=item)
        items.append(
            "{}:{}".format(
                stack["StateMachineArn"].replace("stateMachine", "execution"), job_id
            )
        )
        return item

    yield factory

    empty_table(job_table, "Id", "Sk")
    for arn in items:
        try:
            sf_client.stop_execution(executionArn=arn)
        except Exception as e:
            logger.warning("Unable to stop execution: {}".format(str(e)))


def get_waiter_model(config_file):
    waiter_dir = Path(__file__).parent.parent.joinpath("waiters")
    with open(waiter_dir.joinpath(config_file)) as f:
        config = json.load(f)
    return WaiterModel(config)


@pytest.fixture(scope="session")
def execution_waiter(sf_client):
    waiter_model = get_waiter_model("stepfunctions.json")
    return create_waiter_with_client("ExecutionComplete", waiter_model, sf_client)


@pytest.fixture(scope="session")
def execution_exists_waiter(sf_client):
    waiter_model = get_waiter_model("stepfunctions.json")
    return create_waiter_with_client("ExecutionExists", waiter_model, sf_client)


@pytest.fixture(scope="session")
def job_complete_waiter(ddb_client):
    waiter_model = get_waiter_model("jobs.json")
    return create_waiter_with_client("JobComplete", waiter_model, ddb_client)


@pytest.fixture(scope="session")
def job_finished_waiter(ddb_client):
    waiter_model = get_waiter_model("jobs.json")
    return create_waiter_with_client("JobFinished", waiter_model, ddb_client)


@pytest.fixture(scope="session")
def job_exists_waiter(ddb_client):
    waiter_model = get_waiter_model("jobs.json")
    return create_waiter_with_client("JobExists", waiter_model, ddb_client)


@pytest.fixture(scope="module")
def empty_lake(dummy_lake):
    dummy_lake["bucket"].objects.delete()


@pytest.fixture(scope="session")
def dummy_lake(s3_resource, stack, data_access_role):
    # Lake Config
    bucket_name = "test-" + str(uuid4())
    # Create the bucket and Glue table
    bucket = s3_resource.Bucket(bucket_name)
    policy = s3_resource.BucketPolicy(bucket_name)
    bucket.create(
        CreateBucketConfiguration={
            "LocationConstraint": getenv("AWS_DEFAULT_REGION", "eu-west-1")
        },
    )
    bucket.wait_until_exists()
    s3_resource.BucketVersioning(bucket_name).enable()
    roles = [stack["AthenaExecutionRoleArn"], stack["DeleteTaskRoleArn"]]
    if data_access_role:
        roles.append(data_access_role["Arn"])
    policy.put(
        Policy=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"AWS": roles},
                        "Action": "s3:*",
                        "Resource": [
                            "arn:aws:s3:::{}".format(bucket_name),
                            "arn:aws:s3:::{}/*".format(bucket_name),
                        ],
                    }
                ],
            }
        )
    )

    yield {"bucket_name": bucket_name, "bucket": bucket, "policy": policy}

    # Cleanup
    bucket.objects.delete()
    bucket.object_versions.delete()
    bucket.delete()


@pytest.fixture
def policy_changer(dummy_lake):
    bucket = dummy_lake["bucket"]
    policy = bucket.Policy()
    original = policy.policy

    def update_policy(temp_policy):
        policy.put(Policy=json.dumps(temp_policy))

    yield update_policy
    # reset policy back
    policy.put(Policy=original)


@pytest.fixture
def kms_factory(stack):
    key_id_arn = stack["KMSKeyArns"]
    return key_id_arn.split(",")[0].split("/")[1]


@pytest.fixture
def data_loader(dummy_lake):
    loaded_data = []
    bucket = dummy_lake["bucket"]

    def load_data(filename, object_key, **kwargs):
        file_path = str(Path(__file__).parent.joinpath("data").joinpath(filename))
        bucket.upload_file(file_path, object_key, ExtraArgs=kwargs)
        loaded_data.append(object_key)

    yield load_data

    for d in loaded_data:
        bucket.objects.filter(Prefix=d).delete()
        bucket.object_versions.filter(Prefix=d).delete()


@pytest.fixture
def encrypted_data_loader(dummy_lake, kms_client, data_loader):
    def load_data(filename, object_key, encryption_key, encryption_algorithm, **kwargs):
        file_path = str(Path(__file__).parent.joinpath("data").joinpath(filename))
        with open(file_path, "rb") as f:
            encrypted, metadata = encrypt(
                f,
                {
                    "x-amz-matdesc": json.dumps({"kms_cmk_id": encryption_key}),
                    "x-amz-cek-alg": encryption_algorithm,
                },
                kms_client,
            )
        tmp = tempfile.NamedTemporaryFile()
        with open(tmp.name, "wb") as f:
            f.write(encrypted.read())

        return data_loader(tmp.name, object_key, Metadata=metadata, **kwargs)

    yield load_data


def fetch_total_messages(q):
    return int(q.attributes["ApproximateNumberOfMessages"]) + int(
        q.attributes["ApproximateNumberOfMessagesNotVisible"]
    )


@pytest.fixture(scope="session")
def query_queue(stack):
    queue = boto3.resource("sqs").Queue(stack["QueryQueueUrl"])
    if fetch_total_messages(queue) > 0:
        queue.purge()
    return queue


@pytest.fixture(scope="session")
def fargate_queue(stack):
    queue = boto3.resource("sqs").Queue(stack["DeletionQueueUrl"])
    if fetch_total_messages(queue) > 0:
        queue.purge()
    return queue


@pytest.fixture
def queue_reader(sf_client):
    def read(queue, msgs_to_read=10):
        messages = queue.receive_messages(
            WaitTimeSeconds=5, MaxNumberOfMessages=msgs_to_read
        )
        for message in messages:
            message.delete()
            body = json.loads(message.body)
            if body.get("TaskToken"):
                sf_client.send_task_success(
                    taskToken=body["TaskToken"], output=json.dumps({})
                )

        return messages

    return read


@pytest.fixture(scope="session")
def data_access_role(iam_client):
    try:
        return iam_client.get_role(RoleName="S3F2DataAccessRole")["Role"]
    except ClientError as e:
        logger.warning(str(e))
        pytest.exit("Abandoning test run due to missing data access role", 1)
