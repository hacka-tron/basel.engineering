terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.66.0"
    }
  }
}

resource "aws_ecr_repository" "app" {
  name                 = "glassbox"
  image_tag_mutability = "MUTABLE" # release.yml also pushes a floating :latest tag

  image_scanning_configuration {
    scan_on_push = true
  }
}

# Untagged images accumulate whenever a mutable tag (e.g. :latest) is
# reassigned - the old manifest stays in the repo with no tag pointing at
# it. Each per-commit SHA tag is unique and never reassigned, so this only
# ever cleans up those orphaned :latest predecessors, not real releases.
resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Expire untagged images after 7 days"
      selection = {
        tagStatus   = "untagged"
        countType   = "sinceImagePushed"
        countUnit   = "days"
        countNumber = 7
      }
      action = { type = "expire" }
    }]
  })
}
