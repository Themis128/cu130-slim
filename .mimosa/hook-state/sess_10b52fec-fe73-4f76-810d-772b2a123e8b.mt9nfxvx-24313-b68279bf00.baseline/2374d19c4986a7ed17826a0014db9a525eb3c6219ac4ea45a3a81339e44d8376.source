for f in *.yml; do
  echo "Processing $f"
  # ensure starts with ---
  if ! head -1 "$f" | grep -q '^---$'; then
    sed -i '1i---' "$f"
  fi
  # quote on: line exactly 'on:' (maybe with spaces?)
  sed -i '/^on:$/s/^/\"/' "$f"
  sed -i '/^on:$/s/$/\"/' "$f"
  # remove spaces inside brackets
  sed -i 's/\[ \([^]]*\) \]/[\1]/g' "$f"
  # trim trailing spaces
  sed -i 's/[[:space:]]*$//' "$f"
  # ensure newline at end
  sed -i -e '$a\' "$f"
done
