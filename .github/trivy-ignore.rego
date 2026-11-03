package trivy

# Container images never run their own kernel. The kernel code shipped in
# linux-image, linux-headers, linux-modules, linux-libc-dev and related
# packages is not exploitable inside a container, so suppress these CVEs to
# cut the bulk of scan noise. These packages should still be kept up-to-date,
# but the reported kernel CVEs are not actionable risk for this use-case.
default ignore = false

kernel_pkg_prefixes = {
  "linux-image-",
  "linux-headers-",
  "linux-modules-",
  "linux-modules-extra-",
  "linux-tools-",
  "linux-cloud-tools-",
  "linux-buildinfo-",
  "linux-source-",
  "linux-firmware",
  "linux-libc-dev",
}

ignore {
  prefix := kernel_pkg_prefixes[_]
  startswith(input.PkgName, prefix)
}

ignore {
  input.PkgName == "kernel"
}

ignore {
  input.PkgName == "kernel-headers"
}

ignore {
  input.PkgName == "kernel-devel"
}

# No patched release yet for CVE-2026-69112 in accelerate (affects loading
# untrusted sharded checkpoints via weight_map). local-diffusers only loads a
# fixed trusted MODEL_ID (SD 1.5) and never accepts user-supplied checkpoints.
ignore {
  input.PkgName == "accelerate"
  input.VulnerabilityID == "CVE-2026-69112"
}

# pip 26.x vendors msgpack 1.1.2 inside pip/_vendor for its own internal use —
# Trivy's python-pkg scanner attributes it to the image. The RUNTIME msgpack is
# floored to >=1.2.1 in both pyproject.toml and the Dockerfile (installed 1.2.4+),
# and the vendored copy is a build tool never imported by application code.
ignore {
  input.PkgName == "msgpack"
  input.VulnerabilityID == "CVE-2026-114613"
}

ignore {
  input.PkgName == "msgpack"
  input.VulnerabilityID == "GHSA-6v7p-g79w-8964"
}

# Same class: the setuptools copy Trivy sees (70.3.0) is the toolchain copy, not
# the runtime one — Dockerfile line 20 and pyproject.toml both floor
# setuptools>=78.1.1 (CVE-2025-47273 fixed in 78.1.1) for the app venv.
ignore {
  input.PkgName == "setuptools"
  input.VulnerabilityID == "CVE-2025-47273"
}
