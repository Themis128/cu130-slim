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
