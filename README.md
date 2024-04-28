reorder-python-imports-black
============================

Fork of [reorder-python-imports](https://github.com/asottile/reorder-python-imports) for interoperability with [black](https://github.com/psf/black).

```
pip install reorder-python-imports-black
```

Black wants a newline between the module docstring and the imports, upstream `reorder-python-imports` does not.
Neither project wants to change, if you want to use both in your CI they will get into a fight.

The incompatibility is detailed more [here](https://github.com/psf/black/issues/4175).

Why fork?
=========

Someone already attempted to PR a fix in [reorder-python-imports #370](https://github.com/asottile/reorder-python-imports/pull/370), but it was promptly closed.

The upstream maintainer closes/locks issues about the incompatibility, appears uninterested ([#366](https://github.com/asottile/reorder-python-imports/issues/366), [#367](https://github.com/asottile/reorder-python-imports/issues/367), [#373](https://github.com/asottile/reorder-python-imports/issues/373), [#375](https://github.com/asottile/reorder-python-imports/issues/375)...)

Any other differences?
======================

This package also adds an entry point `rpi`, because it was a drag typing out `reorder-python-imports`.
The original console script remains, though, and they do the same thing.
