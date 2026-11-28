#!/usr/bin/env bash
# 22-python-qt.sh — PySide6 for the host's /usr/bin/python3.
#
# Desktop Python apps that use Kirigami need the distribution's PySide6: the
# PyPI wheel bundles its own Qt and cannot load the system Kirigami QML module.
#
# PySide6 links against Qt's private API, so dnf may satisfy it by moving the
# base image's Qt forward when the live repos are ahead of the base. That is the
# same skew 05-pin-kde-packages.sh guards KDE against (black screen at login),
# so refuse the build if any already-installed qt6 package changes or goes away.
# New qt6 modules pulled in by PySide6 are fine.
set -euo pipefail

qt_packages() {
    rpm -qa 'qt6-*' --qf '%{NAME}.%{ARCH} %{EVR}\n' | LC_ALL=C sort
}

before="$(qt_packages)"

dnf install -y --setopt=install_weak_deps=False python3-pyside6

changed="$(LC_ALL=C comm -23 <(printf '%s\n' "${before}") <(qt_packages))"
if [[ -n "${changed}" ]]; then
    echo "22-python-qt.sh: installing python3-pyside6 changed the base image's Qt:" >&2
    echo "${changed}" >&2
    echo "22-python-qt.sh: advance the BASE_IMAGE pin to a matching Qt instead" >&2
    exit 1
fi
