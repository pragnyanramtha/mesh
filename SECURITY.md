# Security policy

AgentMesh is an alpha project. Do not expose the HTTP hub directly to the
public internet without TLS, network access control, and a rotated admin token.

The hub can route work between agents, but it does not sandbox a worker process.
Run workers in a separate checkout or container with only the credentials and
network access that task requires. Keep model provider keys on the worker, not
in the hub database.

Report security issues privately to the repository owner before opening a public
issue. Include the affected route, a minimal reproduction, and the impact.

