resource "random_id" "state_bucket" {
  byte_length = 8

  # The id is part of the bucket name: replacing it would rename (replace) the
  # state bucket that holds every Terraform state in this project.
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket" "state" {
  bucket = "glassbox-tfstate-${var.aws_account_id}-${random_id.state_bucket.hex}"

  # Holds the state of this root (bootstrap/terraform.tfstate) and of
  # infra/envs/prod. Losing it is an outage, so Terraform must refuse to
  # destroy or replace it. Removing this line is a deliberate, reviewed act.
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket                  = aws_s3_bucket.state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# Bucket-level guardrails. Nothing here widens access.
#   - DenyInsecureTransport: every request must use TLS.
#   - DenyDeleteBucket: nobody (not even the owner) can delete the bucket
#     without first removing this policy, a second deliberate step on top of
#     prevent_destroy.
#   - DenyCiAccessToBootstrapState: the CI roles can never read, write or
#     delete this root's own state (bootstrap/*). That state defines the CI
#     roles' permissions, so CI must not be able to tamper with it. The roles'
#     IAM policies don't grant it either; this explicit deny is a second layer.
#
# The denied role ARNs are built from the account ID and the role names, not
# read from aws_iam_role.*.arn. A reference to a role makes Terraform defer
# reading this document to apply time whenever that role has any pending
# change (a trust-policy edit, say), so the plan then also shows the bucket
# policy as "updated in-place ... (known after apply)" even though the JSON
# comes out identical and apply changes nothing. The resulting ARNs are the
# same strings (the roles have no path); depends_on on the bucket policy below
# keeps the roles created before the policy names them.
locals {
  bootstrap_state_denied_role_arns = [
    for name in ["glassbox-ci", "glassbox-ci-plan", "glassbox-ci-release"] :
    "arn:aws:iam::${var.aws_account_id}:role/${name}"
  ]
}

data "aws_iam_policy_document" "state_bucket" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.state.arn, "${aws_s3_bucket.state.arn}/*"]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }

  statement {
    sid       = "DenyDeleteBucket"
    effect    = "Deny"
    actions   = ["s3:DeleteBucket"]
    resources = [aws_s3_bucket.state.arn]

    principals {
      type        = "*"
      identifiers = ["*"]
    }
  }

  statement {
    sid       = "DenyCiAccessToBootstrapState"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = ["${aws_s3_bucket.state.arn}/bootstrap/*"]

    principals {
      type        = "AWS"
      identifiers = local.bootstrap_state_denied_role_arns
    }
  }
}

resource "aws_s3_bucket_policy" "state" {
  bucket = aws_s3_bucket.state.id
  policy = data.aws_iam_policy_document.state_bucket.json

  # Public access block settings must be in place before a policy is attached,
  # and S3 rejects a policy naming a role that doesn't exist yet.
  depends_on = [
    aws_s3_bucket_public_access_block.state,
    aws_iam_role.ci,
    aws_iam_role.plan,
    aws_iam_role.release,
  ]
}

resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]
}

# This account has GitHub's "immutable subject" OIDC feature on by default
# (confirmed via `gh api repos/hacka-tron/basel.engineering/actions/oidc/customization/sub`,
# which returned use_immutable_subject: true) - the sub claim bakes in the
# permanent numeric owner/repo IDs instead of the plain "owner/repo" name,
# specifically so a repo rename or a name later reused by a different repo
# can't collide with an old trust policy. Confirmed empirically: a real
# workflow run's decoded OIDC token showed
# "repo:hacka-tron@14956857/basel.engineering@1394092219:environment:release",
# not the plain-name format every trust condition below originally assumed
# (and which silently never worked, since nothing had actually exercised
# these roles via a real Actions run until this was discovered). Every OIDC
# trust condition in this file uses this prefix, not a hardcoded plain-name
# string.
locals {
  github_oidc_subject_prefix = "repo:hacka-tron@14956857/basel.engineering@1394092219"

  # State Manager associations of the project's own (glassbox-*) documents.
  ssm_association_resources = [
    "arn:aws:ssm:${var.aws_region}:${var.aws_account_id}:association/*",
    "arn:aws:ssm:${var.aws_region}:${var.aws_account_id}:document/glassbox-*",
  ]
}

# Applies infrastructure changes after approval of the terraform-prod GitHub
# environment.
data "aws_iam_policy_document" "ci_trust" {
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
      values   = ["${local.github_oidc_subject_prefix}:environment:terraform-prod"]
    }
  }
}

resource "aws_iam_role" "ci" {
  name               = "glassbox-ci"
  assume_role_policy = data.aws_iam_policy_document.ci_trust.json
}

data "aws_iam_policy_document" "ci" {
  statement {
    sid       = "RegionalInfrastructure"
    effect    = "Allow"
    actions   = ["ec2:*", "rds:*"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.aws_region]
    }
  }

  # infra/envs/prod's `registry` module manages the ECR repository as a real
  # resource in that state, so every apply refreshes it - not just infra
  # changes that touch it directly. glassbox-ci-plan (read-only) already has
  # narrower describe-only access; this is the actual apply role, so it
  # needs full lifecycle management, not just reads. Found the hard way: the
  # first real automated apply after adding the registry module failed with
  # AccessDeniedException on ecr:DescribeRepositories.
  statement {
    sid       = "ManageGlassboxEcrRepository"
    effect    = "Allow"
    actions   = ["ecr:*"]
    resources = ["arn:aws:ecr:${var.aws_region}:${var.aws_account_id}:repository/glassbox"]
  }

  statement {
    sid    = "RegionalParameterStore"
    effect = "Allow"
    actions = [
      "ssm:AddTagsToResource",
      "ssm:DeleteParameter",
      "ssm:DeleteParameters",
      "ssm:DescribeParameters",
      "ssm:GetParameter",
      "ssm:GetParameters",
      "ssm:ListTagsForResource",
      "ssm:PutParameter",
      "ssm:RemoveTagsFromResource",
    ]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.aws_region]
    }
  }

  # infra/modules/compute/zram.tf: the glassbox-zram-swap Command document
  # and the State Manager association that runs it on the node.
  #
  # Scope: documents are limited to the glassbox- name prefix and can't be
  # shared (no ModifyDocumentPermission). Create/UpdateAssociation are
  # authorized against the document they name and each InstanceIds target,
  # so only glassbox-* documents can be associated (never
  # AWS-RunShellScript), and only with instances tagged project=glassbox.
  #
  # Associations themselves are NOT tag-scoped. Live applies showed that
  # SSM does not populate aws:RequestTag/project on CreateAssociation for the
  # document resource (AccessDenied on document/glassbox-zram-swap even
  # with the tag in the request), so tag conditions here lock CI out.
  #
  # Residual risk: this role can read, change or delete any association in
  # the region (there are no others), and tag-based targets are not checked
  # against any instance. Accepted: the account has exactly one instance, and
  # this role already has ec2:* in the region (it could equally rewrite the
  # instance's user_data).
  statement {
    sid    = "ManageProjectSsmDocuments"
    effect = "Allow"
    actions = [
      "ssm:CreateDocument",
      "ssm:DeleteDocument",
      "ssm:DescribeDocument",
      "ssm:DescribeDocumentPermission",
      "ssm:GetDocument",
      "ssm:ListDocumentVersions",
      "ssm:UpdateDocument",
      "ssm:UpdateDocumentDefaultVersion",
    ]
    resources = ["arn:aws:ssm:${var.aws_region}:${var.aws_account_id}:document/glassbox-*"]
  }

  statement {
    sid    = "ManageProjectSsmAssociations"
    effect = "Allow"
    actions = [
      "ssm:CreateAssociation",
      "ssm:DeleteAssociation",
      "ssm:DescribeAssociation",
      "ssm:UpdateAssociation",
    ]
    resources = local.ssm_association_resources
  }

  # Create/UpdateAssociation are also authorized against each target
  # instance (the first live apply failed with AccessDenied on
  # instance/i-...), so allow them only on instances tagged project=glassbox.
  statement {
    sid       = "AssociateTaggedProjectInstances"
    effect    = "Allow"
    actions   = ["ssm:CreateAssociation", "ssm:UpdateAssociation"]
    resources = ["arn:aws:ec2:${var.aws_region}:${var.aws_account_id}:instance/*"]

    condition {
      test     = "StringEquals"
      variable = "ssm:resourceTag/project"
      values   = ["glassbox"]
    }
  }

  statement {
    sid       = "ListSsmAssociations"
    effect    = "Allow"
    actions   = ["ssm:ListAssociations", "ssm:ListAssociationVersions", "ssm:ListDocuments"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.aws_region]
    }
  }

  statement {
    sid    = "ProjectRolesAndProfiles"
    effect = "Allow"
    actions = [
      "iam:AddRoleToInstanceProfile",
      "iam:AttachRolePolicy",
      "iam:CreateInstanceProfile",
      "iam:CreateRole",
      "iam:DeleteInstanceProfile",
      "iam:DeleteRole",
      "iam:DeleteRolePolicy",
      "iam:DetachRolePolicy",
      "iam:GetInstanceProfile",
      "iam:GetRole",
      "iam:GetRolePolicy",
      "iam:ListAttachedRolePolicies",
      "iam:ListInstanceProfilesForRole",
      "iam:ListRolePolicies",
      "iam:PutRolePolicy",
      "iam:RemoveRoleFromInstanceProfile",
      "iam:TagInstanceProfile",
      "iam:TagRole",
      "iam:UntagInstanceProfile",
      "iam:UntagRole",
      "iam:UpdateAssumeRolePolicy",
      "iam:UpdateRole",
    ]
    resources = [
      "arn:aws:iam::${var.aws_account_id}:role/glassbox-*",
      "arn:aws:iam::${var.aws_account_id}:instance-profile/glassbox-*",
    ]
  }

  statement {
    sid    = "ProjectPolicies"
    effect = "Allow"
    actions = [
      "iam:CreatePolicy",
      "iam:CreatePolicyVersion",
      "iam:DeletePolicy",
      "iam:DeletePolicyVersion",
      "iam:GetPolicy",
      "iam:GetPolicyVersion",
      "iam:ListEntitiesForPolicy",
      "iam:ListPolicyVersions",
      "iam:TagPolicy",
      "iam:UntagPolicy",
    ]
    resources = ["arn:aws:iam::${var.aws_account_id}:policy/glassbox-*"]
  }

  statement {
    sid       = "PassProjectEc2Roles"
    effect    = "Allow"
    actions   = ["iam:PassRole"]
    resources = ["arn:aws:iam::${var.aws_account_id}:role/glassbox-*"]

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ec2.amazonaws.com"]
    }
  }

  statement {
    sid       = "ListStateBucket"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.state.arn]

    # List only what the envs/prod backend needs: its own prefix, the exact
    # state key (the backend lists with prefix = key), and the default
    # workspace-enumeration prefix env:/ (empty, workspaces are not used).
    # Never bootstrap/.
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values = [
        "envs/prod",
        "envs/prod/*",
        "env:/",
        "env:/*",
      ]
    }
  }

  statement {
    sid       = "StateBucketLocation"
    effect    = "Allow"
    actions   = ["s3:GetBucketLocation"]
    resources = [aws_s3_bucket.state.arn]
  }

  # Scoped to the production state prefix only (state object + S3-native
  # .tflock lockfile). Deliberately NOT the whole bucket: CI must not be able
  # to read or change bootstrap/terraform.tfstate, the state that defines
  # CI's own permissions.
  statement {
    sid       = "ReadWriteProductionStateAndLockfile"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.state.arn}/envs/prod/*"]
  }
}

resource "aws_iam_role_policy" "ci" {
  name   = "glassbox-ci-deploy"
  role   = aws_iam_role.ci.id
  policy = data.aws_iam_policy_document.ci.json
}

# Terraform plans run only after approval of the terraform-plan GitHub
# environment. The role can read production resources and state, and can write
# only the S3 lockfile needed by Terraform's remote backend.
data "aws_iam_policy_document" "plan_trust" {
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
      values   = ["${local.github_oidc_subject_prefix}:environment:terraform-plan"]
    }
  }
}

resource "aws_iam_role" "plan" {
  name               = "glassbox-ci-plan"
  assume_role_policy = data.aws_iam_policy_document.plan_trust.json
}

data "aws_iam_policy_document" "plan" {
  statement {
    sid       = "DescribeRegionalInfrastructure"
    effect    = "Allow"
    actions   = ["ec2:Describe*"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.aws_region]
    }
  }

  statement {
    sid       = "ReadProjectIam"
    effect    = "Allow"
    actions   = ["iam:Get*", "iam:List*"]
    resources = ["*"]
  }

  statement {
    sid       = "DescribeParameters"
    effect    = "Allow"
    actions   = ["ssm:DescribeParameters"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.aws_region]
    }
  }

  statement {
    sid     = "ReadProjectParameters"
    effect  = "Allow"
    actions = ["ssm:GetParameter", "ssm:GetParameters", "ssm:ListTagsForResource"]
    resources = [
      "arn:aws:ssm:${var.aws_region}:${var.aws_account_id}:parameter/glassbox/*",
    ]
  }

  # Refresh of the glassbox-zram-swap document and its association
  # (infra/modules/compute/zram.tf), including their tags.
  statement {
    sid    = "ReadProjectSsmDocuments"
    effect = "Allow"
    actions = [
      "ssm:DescribeDocument",
      "ssm:DescribeDocumentPermission",
      "ssm:GetDocument",
      "ssm:ListTagsForResource",
    ]
    resources = ["arn:aws:ssm:${var.aws_region}:${var.aws_account_id}:document/glassbox-*"]
  }

  statement {
    sid       = "ReadTaggedProjectSsmAssociations"
    effect    = "Allow"
    actions   = ["ssm:DescribeAssociation", "ssm:ListTagsForResource"]
    resources = ["arn:aws:ssm:${var.aws_region}:${var.aws_account_id}:association/*"]

    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/project"
      values   = ["glassbox"]
    }
  }

  statement {
    sid       = "ListSsmAssociations"
    effect    = "Allow"
    actions   = ["ssm:ListAssociations", "ssm:ListAssociationVersions", "ssm:ListDocuments"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "aws:RequestedRegion"
      values   = [var.aws_region]
    }
  }

  statement {
    sid       = "ReadPublicAmiParameter"
    effect    = "Allow"
    actions   = ["ssm:GetParameter"]
    resources = ["arn:aws:ssm:${var.aws_region}::parameter/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-arm64"]
  }

  statement {
    sid     = "ReadProjectEcrRepository"
    effect  = "Allow"
    actions = ["ecr:DescribeRepositories", "ecr:GetLifecyclePolicy", "ecr:ListTagsForResource"]
    resources = [
      "arn:aws:ecr:${var.aws_region}:${var.aws_account_id}:repository/glassbox",
    ]
  }

  statement {
    sid       = "DecryptProjectParametersViaSsm"
    effect    = "Allow"
    actions   = ["kms:Decrypt"]
    resources = ["*"]

    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["ssm.${var.aws_region}.amazonaws.com"]
    }
  }

  statement {
    sid       = "ListStateBucket"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.state.arn]

    # List only what the envs/prod backend needs: its own prefix, the exact
    # state key (the backend lists with prefix = key), and the default
    # workspace-enumeration prefix env:/ (empty, workspaces are not used).
    # Never bootstrap/.
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values = [
        "envs/prod",
        "envs/prod/*",
        "env:/",
        "env:/*",
      ]
    }
  }

  statement {
    sid       = "StateBucketLocation"
    effect    = "Allow"
    actions   = ["s3:GetBucketLocation"]
    resources = [aws_s3_bucket.state.arn]
  }

  statement {
    sid       = "ReadProductionState"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.state.arn}/envs/prod/terraform.tfstate"]
  }

  statement {
    sid       = "ManageProductionStateLock"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.state.arn}/envs/prod/terraform.tfstate.tflock"]
  }
}

resource "aws_iam_role_policy" "plan" {
  name   = "glassbox-ci-plan"
  role   = aws_iam_role.plan.id
  policy = data.aws_iam_policy_document.plan.json
}

# Pushes the application image to ECR on every merge to main. Scoped to a
# dedicated "release" GitHub environment (not the terraform-plan/prod ones -
# this never touches infrastructure, only an already-tested image).
#
# The sub condition alone accepts any ref that can use the "release"
# environment, so a manual Release run on an old branch or tag could push a
# higher build-N that Flux deploys. The ref condition also requires the run's
# git ref to be main, independently of the environment's branch policy.
# STS evaluates GitHub claims as condition keys
# (token.actions.githubusercontent.com:ref, IAM condition keys reference,
# OIDC federation, GitHub tab). The immutable subject only changes sub; ref
# is the plain "refs/heads/main". Not job_workflow_ref: it carries the
# mutable owner/repo names and would tie this role to the workflow's file
# name; see infra/CI.md "Release role trust".
data "aws_iam_policy_document" "release_trust" {
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
      values   = ["${local.github_oidc_subject_prefix}:environment:release"]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:ref"
      values   = ["refs/heads/main"]
    }
  }
}

resource "aws_iam_role" "release" {
  name               = "glassbox-ci-release"
  assume_role_policy = data.aws_iam_policy_document.release_trust.json
}

data "aws_iam_policy_document" "release" {
  statement {
    sid       = "EcrAuth"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"] # ECR requires this action to be unscoped
  }

  statement {
    sid    = "PushGlassboxImage"
    effect = "Allow"
    actions = [
      "ecr:BatchCheckLayerAvailability",
      "ecr:BatchGetImage",
      "ecr:CompleteLayerUpload",
      "ecr:DescribeImages",
      "ecr:InitiateLayerUpload",
      "ecr:PutImage",
      "ecr:UploadLayerPart",
    ]
    resources = ["arn:aws:ecr:${var.aws_region}:${var.aws_account_id}:repository/glassbox"]
  }
}

resource "aws_iam_role_policy" "release" {
  name   = "glassbox-ci-release"
  role   = aws_iam_role.release.id
  policy = data.aws_iam_policy_document.release.json
}
