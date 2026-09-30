resource "random_id" "state_bucket" {
  byte_length = 8
}

resource "aws_s3_bucket" "state" {
  bucket = "glassbox-tfstate-${var.aws_account_id}-${random_id.state_bucket.hex}"
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
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [aws_s3_bucket.state.arn]
  }

  statement {
    sid       = "ReadWriteStateAndLockfiles"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.state.arn}/*"]
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
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
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
