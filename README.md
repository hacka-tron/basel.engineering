# Glassbox

> A portfolio you can talk to, with the wiring left in view.

**[Explore basel.engineering](https://basel.engineering)** · [Read the design notes](docs/DESIGN.md)

Hi, I'm Basel. Glassbox is my portfolio and a place to share the work behind it. You can ask about my experience, projects, and skills, or turn the question around and ask how this site works. The answers come with sources, and the architecture view follows each request as it moves through the system.

## Start with a question

| If you're curious about… | Try asking… |
| --- | --- |
| My work | “What did Basel build at Google and Microsoft?” |
| The engineering | “How does this site find the right information?” |
| The decisions | “Why run this on k3s?” |

There's no special prompt to learn. Pick one of the suggested questions on the site or write your own. You can see what was retrieved, where the answer came from, and what the system did along the way.

## Why I built it

I wanted a landing page for my work that felt more like a conversation than a list of links. I also wanted a real project to help me learn retrieval-augmented generation (RAG), embeddings, language models, and the infrastructure needed to run them. Building the whole path from a question to an answer taught me more than another small tutorial would have.

I chose to show the machinery because I think the interesting part of a project is often *how* it works: the decisions, the trade-offs, and the pieces that have to cooperate when someone actually uses it. Glassbox gives me a place to keep learning those things in public.

## Behind the glass

1. **Find context.** The question is matched against curated material about me or the system itself.
2. **Build an answer.** A model uses the relevant passages to respond, with sources you can inspect.
3. **Show the journey.** The page streams the answer and highlights the parts of the system involved in the request.

The frontend is built with React, and the API is built with Python and FastAPI. Redis handles retrieval, caching, and the work queue; MySQL stores the documents. Amazon Bedrock provides the production AI models. The app runs on AWS with k3s, and Terraform, GitHub Actions, and Flux help turn changes in this repo into a running site.

## Take a look around

- [The portfolio content](corpus/about-me/) is the source material for questions about my work.
- [The application](frontend/) and [backend](services/glassbox/) show how the conversation and live diagram work.
- [The design notes](docs/DESIGN.md) explain the architecture and the choices behind it.

## Credits

Lion and rabbit status icons are from Microsoft's [Fluent Emoji](https://github.com/microsoft/fluentui-emoji) (high-contrast set), MIT licensed.
