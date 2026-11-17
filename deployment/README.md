# Kubernetes deployment for proWES

- [Kubernetes deployment for proWES](#kubernetes-deployment-for-wes)
    - [Usage](#usage)
        - [Updates](#updates)
    - [Technical details](#technical-details)
        - [MongoDB](#mongodb)
        - [RabbitMQ](#rabbitmq)
        - [WES](#wes)

The files under this directory can be used to deploy proWES on Kubernetes. The
directory structure is as follows:

- common: YAML files used in all Kubernetes clusters where this is deployed
  - mongodb: YAML for deploying MongoDB
  - rabbitmq: YAML for deploying RabbitMQ
  - wes: YAML for deploying proWES Flask server and proWES Celery worker
- ingress: cluster specific config for ingress (e.g. OpenShift Route or NGINX
  ingress)

## Usage

First you must create a namespace in Kubernetes in which to deploy proWES. The
commands below assume that everything is created in the context of this
namespace. How the namespace is created depends on the cluster, so we won't
document it here.

There are some prerequisites to deploying proWES on Kubernetes. Namely:

- MongoDB:
  - in the same namespace reachable via 'mongodb'
  - DB called `prowes-db` created
  - database-user and database-password for `prowes-db` available in a Secret
    called 'mongodb'
- RabbitMQ:
  - in the same namespace reachable via 'rabbitmq-cluster'
- Secret called `.netrc` created (see below)

You'll need to configure an SFTP server connection using a `.netrc` file with
the following format:

```
machine my-sftp-server.com
login <username>
password <password>
```

Create a Kubernetes Secret from the `.netrc` file:

```bash
kubectl create secret generic netrc --from-file .netrc
```

You need to edit the `values.yaml` file to specify your `applicationDomain` and the `clusterType`

After this you can deploy proWES using `helm`:

```bash
helm install prowes . -f values.yaml
```

### Updates

If you want to edit any of the Deployments, you can update them with
`helm` and the `values.yaml` file. Once edited, you can run this command:

```bash
helm upgrade prowes . -f values.yaml
```

If you want to point to a different FTP server or change the login credentials
for the current FTP server, you can update the `.netrc` secret like so:

```bash
kubectl create secret generic netrc --from-file .netrc --dry-run -o yaml | kubectl apply -f -
```

## Technical details

### MongoDB

The MongoDB database is deployed using:

- `templates/mongodb/mongodb-deployment.yaml`

### RabbitMQ

The message broker RabbitMQ that allows the app to communicate with the worker
is deployed using:

- `templates/rabbitmq/rabbitmq-deployment.yaml`

### WES

proWES consists of five deployments: a Flask server and a Celery worker. These
are deployed using:

- `templates/prowes/prowes-deployment.yaml`
- `templates/prowes/celery-deployment.yaml`

These deployments depend on setting up a shared ReadWriteMany volume between
(`wes-configmap.yaml`).

The image runs as the non-root user `1000` and writes the runs to `/data`. On
Kubernetes (`clusterType: kubernetes`), both deployments set `fsGroup: 1000`
(with `fsGroupChangePolicy: OnRootMismatch`, so that Kubernetes changes the
volume recursively only while `/data` lacks the group), and their init
containers (as root) give `/data` to user and group `1000` where they can
(for volumes whose ownership Kubernetes doesn't manage, e.g., NFS; failures,
e.g., with `root_squash`, are only logged), so that the volume is writable
for it; on OpenShift (`clusterType: openshift`), OpenShift assigns user and
group itself. If `/data` still isn't writable, the app refuses to start
(`PermissionError` in its log).


## Destroy

Simply run:

```bash
helm uninstall prowes
```
