# Roles for the push-button runbooks (.github/workflows/ops.yml) and for
# applying this root from CI (.github/workflows/bootstrap.yml). How to use
# them: infra/CI.md "Runbooks" and "Bootstrap via pipeline".
#
#   glassbox-ops-read        ops-read environment (main only): run the
#                            read-only glassbox-ops-diagnose document, read
#                            results.
#   glassbox-ops             ops environment (owner approval): run the
#                            glassbox-ops-* and glassbox-zram-swap documents,
#                            reboot the glassbox instance.
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
