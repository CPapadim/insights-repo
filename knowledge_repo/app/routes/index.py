""" Define the routes that show all the posts.

This includes:
  - /feed
  - /cluster
  - /table
  - /favorites
"""
import os
import posixpath
import json
from builtins import str
from flask import request, render_template, redirect, Blueprint, current_app, make_response
from flask_login import login_required
from sqlalchemy import case, desc

from .. import permissions
from ..proxies import db_session, current_repo
from ..utils.posts import get_posts, get_post_groups
from ..models import Post, Tag, User, PageView
from ..utils.requests import from_request_get_feed_params, from_request_get_group_params
from ..utils.render import render_post_tldr

blueprint = Blueprint(
    'index', __name__, template_folder='../templates', static_folder='../static')


def has_no_empty_params(rule):
    defaults = rule.defaults if rule.defaults is not None else ()
    arguments = rule.arguments if rule.arguments is not None else ()
    return len(defaults) >= len(arguments)


@blueprint.route("/site-map")
@PageView.logged
def site_map():
    links = []
    for rule in current_app.url_map.iter_rules():
        # Filter out rules we can't navigate to in a browser
        # and rules that require parameters
        # if "GET" in rule.methods and has_no_empty_params(rule):
        # url = url_for(rule.endpoint, **(rule.defaults or {}))
        links.append((str(rule), rule.endpoint))
    # links is now a list of url, endpoint tuples
    return u'<br />'.join(str(link) for link in links)


@blueprint.route('/')
@PageView.logged
def render_index():
    return redirect('/feed')


@blueprint.route('/favorites')
@PageView.logged
@login_required
def render_favorites():
    """ Renders the index-feed view for posts that are liked """

    feed_params = from_request_get_feed_params(request)
    user_id = feed_params['user_id']

    user = (db_session.query(User)
            .filter(User.id == user_id)
            .first())
    posts = user.liked_posts

    post_stats = {post.path: {'all_views': post.view_count,
                              'distinct_views': post.view_user_count,
                              'total_likes': post.vote_count,
                              'total_comments': post.comment_count} for post in posts}

    return render_template("index-feed.html",
                           feed_params=feed_params,
                           posts=posts,
                           post_stats=post_stats,
                           top_header='Favorites')


@blueprint.route('/feed')
@PageView.logged
@permissions.index_view.require()
def render_feed():
    """ Renders the index-feed view """
    feed_params = from_request_get_feed_params(request)
    posts, post_stats = get_posts(feed_params)
    for post in posts:
        post.tldr = render_post_tldr(post)

    request_tag = request.args.get('tag')

    group_params = from_request_get_group_params(request)
    grouped_data = get_post_groups(group_params)

    return render_template('index-feed.html',
                           feed_params=feed_params,
                           posts=posts,
                           post_stats=post_stats,
                           top_header='Knowledge Feed',
                           grouped_data=grouped_data,
                           filters=group_params['filters'],
                           sort_by=group_params['sort_by'],
                           group_by=group_params['group_by'],
                           tag=request_tag)


@blueprint.route('/table')
@PageView.logged
@permissions.index_view.require()
def render_table():
    """Renders the index-table view"""
    feed_params = from_request_get_feed_params(request)
    posts, post_stats = get_posts(feed_params)
    # TODO reference stats inside the template
    return render_template("index-table.html",
                           posts=posts,
                           post_stats=post_stats,
                           top_header="Knowledge Table",
                           feed_params=feed_params)


@blueprint.route('/cluster')
@PageView.logged
@permissions.index_view.require()
def render_cluster():
    """ Render the cluster view """

    request_tag = request.args.get('tag')

    group_params = from_request_get_group_params(request)
    grouped_data = get_post_groups(group_params)

    return render_template("index-cluster.html",
                           grouped_data=grouped_data,
                           filters=group_params['filters'],
                           sort_by=group_params['sort_by'],
                           group_by=group_params['group_by'],
                           tag=request_tag)


@blueprint.route('/create')
@blueprint.route('/create/<knowledge_format>')
@PageView.logged
@permissions.post_view.require()
def create(knowledge_format=None):
    """ Renders the create knowledge view """
    if knowledge_format is None:
        return render_template("create-knowledge.html",
                               web_editor_enabled=current_app.config['WEB_EDITOR_PREFIXES'] != [])

    cur_dir = os.path.dirname(os.path.realpath(__file__))
    knowledge_template = "knowledge_template.{}".format(knowledge_format)
    filename = os.path.join(cur_dir, '../../templates', knowledge_template)
    response = make_response(open(filename).read())
    response.headers["Content-Disposition"] = "attachment; filename=" + knowledge_template
    return response


@blueprint.route('/ajax/index/typeahead', methods=['GET', 'POST'])
def ajax_post_typeahead():
    if not permissions.index_view.can():
        return '[]'

    # this a string of the search term
    search_terms = request.args.get('search', '')
    search_terms = search_terms.split(" ")
    case_statements = []
    for term in search_terms:
        case_stmt = case([(Post.keywords.ilike('%' + term.strip() + '%'), 1)], else_=0)
        case_statements += [case_stmt]

    match_score = sum(case_statements).label("match_score")

    posts = (db_session.query(Post, match_score)
                       .filter(Post.status == current_repo.PostStatus.PUBLISHED.value)
                       .order_by(desc(match_score))
                       .limit(5)
                       .all())

    matches = []
    for (post, count) in posts:
        authors_str = [author.format_name for author in post.authors]
        typeahead_entry = {'author': authors_str,
                           'title': str(post.title),
                           'path': str(post.path),
                           'keywords': str(post.keywords)}
        matches += [typeahead_entry]
    return json.dumps(matches)


@blueprint.route('/ajax/index/typeahead_tags')
@blueprint.route('/ajax_tags_typeahead', methods=['GET'])
def generate_tags_typeahead():
    if not permissions.index_view.can():
        return '[]'
    return json.dumps([t[0] for t in db_session.query(Tag.name).all()])


@blueprint.route('/ajax/index/typeahead_users')
@blueprint.route('/ajax_users_typeahead', methods=['GET'])
def generate_users_typeahead():
    if not permissions.index_view.can():
        return '[]'
    return json.dumps([u[0] for u in db_session.query(User.identifier).all()])


@blueprint.route('/ajax/index/typeahead_paths')
@blueprint.route('/ajax_paths_typeahead', methods=['GET'])
def generate_projects_typeahead():
    if not permissions.index_view.can():
        return '[]'
    # return path stubs for all repositories
    stubs = [u'/'.join(p.split('/')[:-1]) for p in current_repo.dir()]
    return json.dumps(list(set(stubs)))