from django.shortcuts import render_to_response, get_object_or_404
from django.template import RequestContext
from django.http import HttpResponseRedirect
from django.core.urlresolvers import reverse
from django.contrib.auth.decorators import login_required
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.utils.datastructures import SortedDict
from django.utils.translation import ugettext_lazy as _
from django.http import HttpResponse
from django.core import serializers


from django.conf import settings

if "notification" in settings.INSTALLED_APPS:
    from notification import models as notification
else:
    notification = None

from projects.models import Project, ProjectMember, Iteration, Story
from projects.forms import ProjectForm, ProjectUpdateForm, AddUserForm, IterationForm, StoryForm

TOPIC_COUNT_SQL = """
SELECT COUNT(*)
FROM topics_topic
WHERE
    topics_topic.object_id = projects_project.id AND
    topics_topic.content_type_id = %s
"""
MEMBER_COUNT_SQL = """
SELECT COUNT(*)
FROM projects_projectmember
WHERE projects_projectmember.project_id = projects_project.id
"""





def greet(request):
  print "GREETING REQUEST!"
  return render_to_response("greet.html")


def rpc_stories(request, group_slug):
  project = get_object_or_404(Project, slug=group_slug)  
  backlog = Story.objects.filter(project=project, iteration=project.get_default_iteration() )
 
  json_serializer = serializers.get_serializer("json")()
  backlog_json = json_serializer.serialize(backlog, ensure_ascii=False)
  return HttpResponse(backlog_json) #, mimetype='application/json'

@login_required
def reorder_story( request, group_slug, story_id):
  story = get_object_or_404( Story, id=story_id )
  project = get_object_or_404( Project, slug=group_slug )
  if request.method == 'POST':
    rank = 0
    target_rank = int(request.POST["index"])
    story.rank = target_rank;
    story.save()
    
    # For now, a stupid reorder everything algorithm
    for otherStory in project.stories.all().order_by("rank"):
      if rank == target_rank:
        rank+=1
        
      if otherStory != story:        
        otherStory.rank = rank
        otherStory.save()      
        rank = rank + 1
        
    return HttpResponse("OK")
  return   render_to_response("stories/story.html", {
      "story": story
    }, context_instance=RequestContext(request))
  
  
  
  
@login_required
def stories_iteration(request, group_slug, iteration_id):
  project = get_object_or_404(Project, slug=group_slug)  
  iteration = get_object_or_404(Iteration, id=iteration_id, project=project)  
  stories = Story.objects.filter(project=project, iteration=iteration ).order_by("rank")
  return render_to_response("stories/mini_story_list.html", {
    "stories": stories
  }, context_instance=RequestContext(request))


@login_required
def stories(request, group_slug):
  project = get_object_or_404(Project, slug=group_slug)  
  
  if request.method == 'POST': # If the form has been submitted...
    form = StoryForm(request.POST) # A form bound to the POST data
    if form.is_valid(): # All validation rules pass
      story = form.save( commit=False )
      story.local_id = project.stories.count() + 1
      story.project = project
      story.creator = request.user
      story.iteration = project.get_default_iteration()
      story.save()
      form = StoryForm()
  else:
    form = StoryForm()
  
 
  
  return render_to_response("stories/story_list.html", {
      "form": form,
      "project": project,
 
  }, context_instance=RequestContext(request))

@login_required
def create(request, form_class=ProjectForm, template_name="projects/create.html"):
    project_form = form_class(request.POST or None)
    
    if project_form.is_valid():
        project = project_form.save(commit=False)
        project.creator = request.user
        project.save()
        
        project_member = ProjectMember(project=project, user=request.user)
        project.members.add(project_member)
        project_member.save()        
        
        default_iteration = Iteration( name='Backlog', detail='', default_iteration=True)
        project.iterations.add(default_iteration)
        default_iteration.save()        
        if notification:
            # @@@ might be worth having a shortcut for sending to all users
            notification.send(User.objects.all(), "projects_new_project",
                {"project": project}, queue=True)
        return HttpResponseRedirect(project.get_absolute_url())
    
    return render_to_response(template_name, {
        "project_form": project_form,
    }, context_instance=RequestContext(request))


@login_required
def iteration_create(request, group_slug=None):
  project = get_object_or_404(Project, slug=group_slug)  
  is_member = project.user_is_member(request.user)
  
  if not is_member:
    return HttpResponseForbidden()
  
  if request.method == 'POST': # If the form has been submitted...
    form = IterationForm(request.POST) # A form bound to the POST data
    if form.is_valid(): 
      iteration = form.save(commit=False)
      iteration.project = project
      iteration.save()      
      return HttpResponseRedirect( reverse('project_detail', kwargs={'group_slug':project.slug}) ) # Redirect after POST
  else:
    form = IterationForm() # An unbound form

  return render_to_response('projects/iteration.html', { 'form': form,  }, context_instance=RequestContext(request))



@login_required
def projects(request, template_name="projects/projects.html"):
    return your_projects(request, template_name)

def delete(request, group_slug=None, redirect_url=None):
    project = get_object_or_404(Project, slug=group_slug)
    if not redirect_url:
        redirect_url = reverse('project_list')
    
    # @@@ eventually, we'll remove restriction that project.creator can't leave project but we'll still require project.members.all().count() == 1
    if (request.user.is_authenticated() and request.method == "POST" and
            request.user == project.creator and project.members.all().count() == 1):
        project.delete()
        request.user.message_set.create(message=_("Project %(project_name)s deleted.") % {"project_name": project.name})
        # no notification required as the deleter must be the only member
    
    return HttpResponseRedirect(redirect_url)


@login_required
def your_projects(request, template_name="projects/your_projects.html"):

    projects = Project.objects.filter(member_users=request.user).order_by("name")

    content_type = ContentType.objects.get_for_model(Project)

    projects = projects.extra(select=SortedDict([
        ('member_count', MEMBER_COUNT_SQL),
        ('topic_count', TOPIC_COUNT_SQL),
    ]), select_params=(content_type.id,))

    return render_to_response(template_name, {
        "projects": projects,
    }, context_instance=RequestContext(request))


def project(request, group_slug=None, form_class=ProjectUpdateForm, adduser_form_class=AddUserForm,
        template_name="projects/project.html"):        
    project = get_object_or_404(Project, slug=group_slug)
    
    if not request.user.is_authenticated():
        is_member = False
    else:
        is_member = project.user_is_member(request.user)
    
    action = request.POST.get("action")
    if request.user == project.creator and action == "update":
        project_form = form_class(request.POST, instance=project)
        if project_form.is_valid():
            project = project_form.save()
    else:
        project_form = form_class(instance=project)
    if request.user == project.creator and action == "add":
        adduser_form = adduser_form_class(request.POST, project=project)
        if adduser_form.is_valid():
            adduser_form.save(request.user)
            adduser_form = adduser_form_class(project=project) # clear form
    else:
        adduser_form = adduser_form_class(project=project)
    
    return render_to_response(template_name, {
        "project_form": project_form,
        "adduser_form": adduser_form,
        "project": project,
        "group": project, # @@@ this should be the only context var for the project
        "is_member": is_member,
    }, context_instance=RequestContext(request))