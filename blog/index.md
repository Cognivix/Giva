---
layout: home
title: Blog Posts
---

# Latest Updates

Check out my recent posts below:

<ul>
  {% for post in site.posts %}
    <li>
      <span class="post-date">{{ post.date | date: "%b %-d, %Y" }}</span>
      <a href="{{ post.url | relative_url }}">{{ post.title }}</a>
    </li>
  {% endfor %}
</ul>

{% if site.posts.size == 0 %}
  <p>No posts yet. Stay tuned!</p>
{% endif %}
