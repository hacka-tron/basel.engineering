# Roles for the push-button runbooks (.github/workflows/ops.yml) and for
# applying this root from CI (.github/workflows/bootstrap.yml). How to use
# them: infra/CI.md "Runbooks" and "Bootstrap via pipeline".
#
#   glassbox-ops-read        ops-read environment (main only): run the
#                            read-only glassbox-ops-diagnose document, read
#                            results, list snapshots, alarms and restore
#                            tasks.
#   glassbox-ops             ops environment (owner approval): run the
#                            glassbox-ops-* and glassbox-zram-swap documents,
#                            reboot the glassbox instance, replace its root
#                            volume (restore-snapshot; see the residual-risk
#                            note at ReplaceGlassboxRootVolume).
#   glassbox-bootstrap-plan  bootstrap-plan environment: read-only plan of
#                            this root.
#   glassbox-bootstrap       bootstrap environment (owner approval, main
#                            only): apply this root.
#
# Every trust condition uses local.github_oidc_subject_prefix (immutable
# subject, see main.tf). An environment's GitHub protection rules (required
# reviewer, main-only deployment branch policy) are what make an
# "environment:<name>" subject trustworthy; the gh api commands that create
# them are in infra/CI.md.

locals {
  # role => exact OIDC subjects allowed to assume it.
  runbook_role_subjects = {
    # Only the environment subject. The plain ref:refs/heads/main subject was
    # dropped: the one caller (ops.yml's diagnose job) always runs in
    # ops-read, and any other main job with id-token: write would otherwise
    # be able to run diagnose.
    "glassbox-ops-read"       = ["${local.github_oidc_subject_prefix}:environment:ops-read"]
    "glassbox-ops"            = ["${local.github_oidc_subject_prefix}:environment:ops"]
    "glassbox-bootstrap-plan" = ["${local.github_oidc_subject_prefix}:environment:bootstrap-plan"]
    "glassbox-bootstrap"      = ["${local.github_oidc_subject_prefix}:environment:bootstrap"]
  }

  ssm_document_arn_prefix = "arn:aws:ssm:${var.aws_region}:${var.aws_account_id}:document"
  ec2_instances_arn       = "arn:aws:ec2:${var.aws_region}:${var.aws_account_id}:instance/*"

  # The production node carries both tags (Name from infra/modules/compute,
  # project from the provider's default_tags). The account has no other
  # instance, but the conditions keep it that way if one is ever added.
  glassbox_instance_tags = {
    Name    = "glassbox"
    project = "glassbox"
  }

  # Tags the daily DLM policy puts on every snapshot it takes
  # (infra/modules/compute/snapshots.tf, tags_to_add). The restore runbook
  # may only use snapshots that carry both.
  glassbox_snapshot_tags = {
    project         = "glassbox"
    glassbox-backup = "daily-root"
  }

  # Read-only listing for diagnose and list-snapshots: the daily snapshots,
  # root volume replacement tasks, detached volumes (old root volumes kept by
  # restores) and the glassbox-* alarms. None of these
  # calls has resource-level permissions except DescribeAlarms, which is
  # kept region-wide for the same reason as in glassbox-ci (main.tf).
  backup_read_actions = [
    "cloudwatch:DescribeAlarms",
    "ec2:DescribeReplaceRootVolumeTasks",
    "ec2:DescribeSnapshots",
    "ec2:DescribeVolumes",
  ]

  bootstrap_state_key = "bootstrap/terraform.tfstate"
}

data "aws_iam_policy_document" "runbook_trust" {
  for_each = local.runbook_role_subjects

  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = each.value
    }
  }
}

resource "aws_iam_role" "runbook" {
  for_each = local.runbook_role_subjects

  name                 = each.key
  assume_role_policy   = data.aws_iam_policy_document.runbook_trust[each.key].json
  max_session_duration = 3600
}

# ---------------------------------------------------------------------------
# glassbox-ops-read: diagnose only.
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "ops_read" {
  # ssm:SendCommand is authorized against the document AND each target
  # instance, so it needs both statements. Only the read-only diagnose
  # document: never AWS-RunShellScript or any other document.
  statement {
    sid       = "SendDiagnoseDocument"
    effect    = "Allow"
    actions   = ["ssm:SendCommand"]
    resources = ["${local.ssm_document_arn_prefix}/glassbox-ops-diagnose"]
  }

  statement {
    sid       = "SendCommandToGlassboxInstance"
    effect    = "Allow"
    actions   = ["ssm:SendCommand"]
    resources = [local.ec2_instances_arn]

    dynamic "condition" {
      for_each = local.glassbox_instance_tags
      content {
        test     = "StringEquals"
        variable = "ssm:resourceTag/${condition.key}"
        values   = [condition.value]
      }
    }
  }

  # These calls have no resource-level permissions in IAM.
  statement {
    sid    = "ReadCommandResultsAndInstanceState"
    effect = "Allow"
    actions = [
      "ec2:DescribeInstanceStatus",
      "ec2:DescribeInstances",
      "ssm:DescribeInstanceInformation",
      "ssm:GetCommandInvocation",
      "ssm:ListCommandInvocations",
    ]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.aws_region]
    }
  }

  statement {
    sid       = "ListSnapshotsAlarmsAndRestoreTasks"
    effect    = "Allow"
    actions   = local.backup_read_actions
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.aws_region]
    }
  }
}

resource "aws_iam_role_policy" "ops_read" {
  name   = "glassbox-ops-read"
  role   = aws_iam_role.runbook["glassbox-ops-read"].id
  policy = data.aws_iam_policy_document.ops_read.json
}

# ---------------------------------------------------------------------------
# glassbox-ops: every runbook action, behind the ops environment's approval.
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "ops" {
  # glassbox-ops-* documents are infra/modules/ops; glassbox-zram-swap is
  # infra/modules/compute/zram.tf. Both are Terraform-managed, so only
  # reviewed scripts can run. glassbox-ci can also create glassbox-*
  # documents, but only through a reviewed, approved Terraform apply.
  statement {
    sid    = "SendProjectRunbookDocuments"
    effect = "Allow"
    actions = [
      "ssm:SendCommand",
    ]
    resources = [
      "${local.ssm_document_arn_prefix}/glassbox-ops-*",
      "${local.ssm_document_arn_prefix}/glassbox-zram-swap",
    ]
  }

  statement {
    sid       = "SendCommandToGlassboxInstance"
    effect    = "Allow"
    actions   = ["ssm:SendCommand"]
    resources = [local.ec2_instances_arn]

    dynamic "condition" {
      for_each = local.glassbox_instance_tags
      content {
        test     = "StringEquals"
        variable = "ssm:resourceTag/${condition.key}"
        values   = [condition.value]
      }
    }
  }

  # apply-zram checks that the live glassbox-zram-swap document still matches
  # the script at the workflow's commit before running it.
  statement {
    sid       = "ReadZramDocument"
    effect    = "Allow"
    actions   = ["ssm:GetDocument"]
    resources = ["${local.ssm_document_arn_prefix}/glassbox-zram-swap"]
  }

  statement {
    sid       = "RebootGlassboxInstance"
    effect    = "Allow"
    actions   = ["ec2:RebootInstances"]
    resources = [local.ec2_instances_arn]

    dynamic "condition" {
      for_each = local.glassbox_instance_tags
      content {
        test     = "StringEquals"
        variable = "aws:ResourceTag/${condition.key}"
        values   = [condition.value]
      }
    }
  }

  # restore-snapshot: EC2 "replace root volume" (CreateReplaceRootVolumeTask).
  # IAM authorizes it against the instance, the source snapshot (if one is
  # named), and the volume and task involved. Instance: only the tagged
  # glassbox node. Snapshot: when the request names one, only a snapshot the
  # daily DLM policy took (its tags). The runbook passes no TagSpecifications,
  # so no ec2:CreateTags is needed. No DeleteVolume/DeleteSnapshot: with
  # old_volume=delete, EC2 deletes the replaced root volume as part of the
  # task.
  #
  # Residual risk (not enforced by IAM): the snapshot statement only applies
  # when a snapshot is named. A launch-state replacement (no snapshot: back to
  # the AMI's original disk) or a replacement with an existing detached
  # volume (--volume-id, authorized by the unconditioned volume/* statement;
  # volumes have no aws:ResourceTag key for this action) would likely pass.
  # No condition key cleanly tells those modes apart: ec2:SnapshotID exists
  # only on the snapshot resource, so a Null-condition Deny would also deny
  # the instance and task resources of every legitimate request. Those modes
  # are blocked only by the reviewed script (it always passes a validated
  # daily snapshot) plus the owner's ops approval, i.e. the same trust as
  # every other action of this role.
  statement {
    sid       = "ReplaceGlassboxRootVolume"
    effect    = "Allow"
    actions   = ["ec2:CreateReplaceRootVolumeTask"]
    resources = [local.ec2_instances_arn]

    dynamic "condition" {
      for_each = local.glassbox_instance_tags
      content {
        test     = "StringEquals"
        variable = "aws:ResourceTag/${condition.key}"
        values   = [condition.value]
      }
    }
  }

  statement {
    sid       = "ReplaceRootFromDailySnapshotsOnly"
    effect    = "Allow"
    actions   = ["ec2:CreateReplaceRootVolumeTask"]
    resources = ["arn:aws:ec2:${var.aws_region}::snapshot/*"]

    dynamic "condition" {
      for_each = local.glassbox_snapshot_tags
      content {
        test     = "StringEquals"
        variable = "aws:ResourceTag/${condition.key}"
        values   = [condition.value]
      }
    }
  }

  # A restore reboots the node, and a slow boot must not trip the reboot
  # alarm into a second reboot mid-restore: the runbook disables that alarm's
  # actions for the duration and re-enables them on every exit path. Only
  # that one alarm.
  statement {
    sid     = "PauseRebootAlarmDuringRestore"
    effect  = "Allow"
    actions = ["cloudwatch:DisableAlarmActions", "cloudwatch:EnableAlarmActions"]
    resources = [
      "arn:aws:cloudwatch:${var.aws_region}:${var.aws_account_id}:alarm:glassbox-node-reboot",
    ]
  }

  statement {
    sid     = "ReplaceRootCreatesVolumeAndTask"
    effect  = "Allow"
    actions = ["ec2:CreateReplaceRootVolumeTask"]
    resources = [
      "arn:aws:ec2:${var.aws_region}:${var.aws_account_id}:volume/*",
      "arn:aws:ec2:${var.aws_region}:${var.aws_account_id}:replace-root-volume-task/*",
    ]
  }

  # The root volume is encrypted. With the AWS managed aws/ebs key its key
  # policy already lets EC2 use it on the caller's behalf; this covers a
  # customer managed default key too. Only through EC2 in this region, and
  # grants only for AWS resources (the new volume).
  statement {
    sid       = "GrantEbsKeyToEc2Volumes"
    effect    = "Allow"
    actions   = ["kms:CreateGrant"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ec2.${var.aws_region}.amazonaws.com"]
    }

    condition {
      test     = "Bool"
      variable = "kms:GrantIsForAWSResource"
      values   = ["true"]
    }
  }

  statement {
    sid    = "UseEbsKeyThroughEc2"
    effect = "Allow"
    actions = [
      "kms:Decrypt",
      "kms:DescribeKey",
      "kms:GenerateDataKeyWithoutPlaintext",
      "kms:ReEncryptFrom",
      "kms:ReEncryptTo",
    ]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ec2.${var.aws_region}.amazonaws.com"]
    }
  }

  statement {
    sid    = "ReadCommandResultsAndInstanceState"
    effect = "Allow"
    actions = [
      "ec2:DescribeInstanceStatus",
      "ec2:DescribeInstances",
      "ssm:DescribeInstanceInformation",
      "ssm:GetCommandInvocation",
      "ssm:ListCommandInvocations",
    ]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.aws_region]
    }
  }

  statement {
    sid       = "ListSnapshotsAlarmsAndRestoreTasks"
    effect    = "Allow"
    actions   = local.backup_read_actions
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.aws_region]
    }
  }
}

resource "aws_iam_role_policy" "ops" {
  name   = "glassbox-ops"
  role   = aws_iam_role.runbook["glassbox-ops"].id
  policy = data.aws_iam_policy_document.ops.json
}

# ---------------------------------------------------------------------------
# glassbox-bootstrap-plan: read-only plan of this root (PRs and dispatch).
# It runs `terraform plan -lock=false`, so it needs no write access at all.
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "bootstrap_plan" {
  statement {
    sid       = "ReadIam"
    effect    = "Allow"
    actions   = ["iam:Get*", "iam:List*"]
    resources = ["*"]
  }

  # Bucket-level reads only (versioning, encryption, policy, public access
  # block, ...): s3:Get* on the bucket ARN can't read objects.
  statement {
    sid       = "ReadStateBucketConfiguration"
    effect    = "Allow"
    actions   = ["s3:Get*", "s3:List*"]
    resources = [aws_s3_bucket.state.arn]
  }

  statement {
    sid       = "ReadBootstrapState"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.state.arn}/${local.bootstrap_state_key}"]
  }
}

resource "aws_iam_role_policy" "bootstrap_plan" {
  name   = "glassbox-bootstrap-plan"
  role   = aws_iam_role.runbook["glassbox-bootstrap-plan"].id
  policy = data.aws_iam_policy_document.bootstrap_plan.json
}

# ---------------------------------------------------------------------------
# glassbox-bootstrap: applies this root. Effectively administrator of CI's
# IAM and of the state bucket, by design: this root defines every CI role, so
# whatever applies it can change any of them, including this role itself.
# The guardrail is the bootstrap GitHub environment (owner is the required
# reviewer, main branch only) plus the reviewed plan the apply must match,
# not this policy. The policy only keeps the blast radius to what this root
# manages: glassbox-* roles and policies, the GitHub OIDC provider, and the
# state bucket. It has no EC2, SSM, ECR or other data access.
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "bootstrap" {
  statement {
    sid       = "ReadIam"
    effect    = "Allow"
    actions   = ["iam:Get*", "iam:List*"]
    resources = ["*"]
  }

  statement {
    sid     = "ManageProjectRolesAndPolicies"
    effect  = "Allow"
    actions = ["iam:*"]
    resources = [
      "arn:aws:iam::${var.aws_account_id}:role/glassbox-*",
      "arn:aws:iam::${var.aws_account_id}:policy/glassbox-*",
      aws_iam_openid_connect_provider.github.arn,
    ]
  }

  # Everything on the state bucket and its objects (state of both roots).
  # The bucket policy from the bootstrap state change still denies
  # s3:DeleteBucket to everyone; it denies bootstrap/* only to the CI roles
  # (glassbox-ci, -ci-plan, -ci-release), not to this role.
  statement {
    sid       = "ManageStateBucket"
    effect    = "Allow"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.state.arn, "${aws_s3_bucket.state.arn}/*"]
  }
}

resource "aws_iam_role_policy" "bootstrap" {
  name   = "glassbox-bootstrap"
  role   = aws_iam_role.runbook["glassbox-bootstrap"].id
  policy = data.aws_iam_policy_document.bootstrap.json
}
