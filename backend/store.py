"""Nira's issue/source/resolution model, expanded to a portfolio; Dive-style parent tasks.
SQLite is supported only for local tests. Hosted storage uses the existing MariaDB.
"""
from __future__ import annotations
import datetime as dt
import json
import re
import uuid
from contextlib import contextmanager
from sqlalchemy import create_engine, Column, String, Text, Integer, ForeignKey, UniqueConstraint, select, func, or_, and_, event, update
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.exc import IntegrityError
from sqlalchemy.dialects.mysql import LONGTEXT

Base = declarative_base()
LARGE_TEXT = Text().with_variant(LONGTEXT(), 'mysql', 'mariadb')
STATUSES = ('backlog','ready','in_progress','blocked','done')
PROJECT_STATUSES = ('active','paused','idea','completed','needs_review')
PRIORITIES = ('critical','high','medium','low')
TYPES = ('epic','task','bug','research')
def uid(): return str(uuid.uuid4())
def now(): return dt.datetime.now(dt.timezone.utc).isoformat(timespec='seconds')
class DomainError(Exception):
    def __init__(self, message, status=400): self.message=message; self.status=status; super().__init__(message)

class Project(Base):
    __tablename__='projects'
    id=Column(String(36),primary_key=True,default=uid)
    key=Column(String(16),unique=True,nullable=False)
    name=Column(String(160),nullable=False)
    description=Column(LARGE_TEXT,nullable=False,default='')
    status=Column(String(24),nullable=False,default='active')
    category=Column(String(40),nullable=False,default='Product')
    owner=Column(String(80),nullable=False,default='Evan')
    source=Column(LARGE_TEXT,nullable=False,default='')
    source_key=Column(String(180),unique=True)
    version=Column(Integer,nullable=False,default=1)
    next_number=Column(Integer,nullable=False,default=1)
    created_at=Column(String(40),nullable=False,default=now)
    updated_at=Column(String(40),nullable=False,default=now)
class Issue(Base):
    __tablename__='issues'
    id=Column(String(36),primary_key=True,default=uid)
    project_id=Column(String(36),ForeignKey('projects.id'),nullable=False,index=True)
    number=Column(Integer,nullable=False)
    parent_id=Column(String(36),ForeignKey('issues.id'),index=True)
    title=Column(String(240),nullable=False)
    description=Column(LARGE_TEXT,nullable=False,default='')
    status=Column(String(24),nullable=False,default='backlog',index=True)
    priority=Column(String(16),nullable=False,default='medium')
    type=Column(String(16),nullable=False,default='task')
    owner=Column(String(80),nullable=False,default='Evan')
    due_date=Column(String(10))
    labels=Column(Text,nullable=False,default='[]')
    source=Column(LARGE_TEXT,nullable=False,default='')
    source_key=Column(String(180),unique=True)
    resolution=Column(LARGE_TEXT,nullable=False,default='')
    version=Column(Integer,nullable=False,default=1)
    created_at=Column(String(40),nullable=False,default=now)
    updated_at=Column(String(40),nullable=False,default=now)
    __table_args__=(UniqueConstraint('project_id','number'),)
class Comment(Base):
    __tablename__='comments'
    id=Column(String(36),primary_key=True,default=uid)
    issue_id=Column(String(36),ForeignKey('issues.id'),nullable=False,index=True)
    body=Column(LARGE_TEXT,nullable=False)
    author=Column(String(80),nullable=False,default='Evan')
    created_at=Column(String(40),nullable=False,default=now)
class Activity(Base):
    __tablename__='activity'
    id=Column(Integer,primary_key=True,autoincrement=True)
    entity_id=Column(String(36),nullable=False,index=True)
    action=Column(String(80),nullable=False)
    detail=Column(LARGE_TEXT,nullable=False,default='')
    actor=Column(String(80),nullable=False,default='Evan')
    created_at=Column(String(40),nullable=False,default=now)
class Link(Base):
    __tablename__='links'
    id=Column(String(36),primary_key=True,default=uid)
    issue_a=Column(String(36),ForeignKey('issues.id'),nullable=False,index=True)
    issue_b=Column(String(36),ForeignKey('issues.id'),nullable=False,index=True)
    kind=Column(String(16),nullable=False,default='related')
    __table_args__=(UniqueConstraint('issue_a','issue_b','kind'),)

class DependencyGraph(Base):
    __tablename__='dependency_graph'
    id=Column(Integer,primary_key=True)
    generation=Column(Integer,nullable=False,default=0)

class Token(Base):
    __tablename__='tokens'
    id=Column(String(36),primary_key=True,default=uid)
    name=Column(String(120),nullable=False)
    digest=Column(String(64),unique=True,nullable=False)
    scope=Column(String(16),nullable=False,default='read')
    revoked=Column(Integer,nullable=False,default=0)
    created_at=Column(String(40),nullable=False,default=now)
class LoginSession(Base):
    __tablename__='sessions'
    digest=Column(String(64),primary_key=True)
    csrf=Column(String(80),nullable=False)
    expires=Column(Integer,nullable=False)

def clean_text(value,limit,field,required=False):
    if not isinstance(value,str) or len(value)>limit or (required and not value.strip()): raise DomainError(f'Invalid {field}')
    return value.strip()
def choice(value,choices,field):
    if value not in choices: raise DomainError(f'Invalid {field}')
    return value

def serialize(row):
    result={c.name:getattr(row,c.name) for c in row.__table__.columns}
    if 'labels' in result: result['labels']=json.loads(result['labels'])
    return result
class Store:
    def __init__(self,url):
        self.engine=create_engine(url,pool_pre_ping=True,**({'connect_args':{'check_same_thread':False}} if url.startswith('sqlite') else {'pool_size':4,'max_overflow':2}))
        if url.startswith('sqlite'):
            @event.listens_for(self.engine,'connect')
            def foreign_keys(connection,record): connection.execute('PRAGMA foreign_keys=ON')
        self.Session=sessionmaker(self.engine,expire_on_commit=False)
    def initialize(self):
        Base.metadata.create_all(self.engine)
        try:
            with self.tx() as s:
                if s.get(DependencyGraph, 1) is None:
                    s.add(DependencyGraph(id=1))
        except DomainError as error:
            if error.status != 409:
                raise
    @contextmanager
    def tx(self):
        with self.Session() as s:
            try:
                with s.begin(): yield s
            except IntegrityError as e: raise DomainError('A record with that key already exists',409) from e
    def project(self,s,ref,lock=False):
        statement=select(Project).where(or_(Project.id==ref,Project.key==ref))
        if lock: statement=statement.with_for_update()
        p=s.scalar(statement)
        if not p: raise DomainError('Project not found',404)
        return p
    def issue(self,s,ref,lock=False):
        statement=select(Issue).where(Issue.id==ref)
        if re.fullmatch(r'[A-Z][A-Z0-9_]{1,15}-\d+',ref):
            key,number=ref.rsplit('-',1);statement=select(Issue).join(Project).where(Project.key==key,Issue.number==int(number))
        if lock: statement=statement.with_for_update()
        i=s.scalar(statement)
        if not i: raise DomainError('Issue not found',404)
        return i
    def issue_dict(self,s,i):
        d=serialize(i);p=s.get(Project,i.project_id);d['key']=f'{p.key}-{i.number}';d['project_key']=p.key;d['project_name']=p.name
        return d
    def log(self,s,entity,action,detail='',actor='Evan'): s.add(Activity(entity_id=entity,action=action,detail=detail,actor=actor))
    def create_project(self,data,actor='Evan'):
        allowed={'key','name','description','status','category','owner','source','source_key'}
        if set(data)-allowed: raise DomainError('Unknown project fields')
        key=clean_text(data.get('key',''),16,'key',True).upper()
        if data.get('source_key') is not None:clean_text(data['source_key'],180,'source_key',True)
        if not re.fullmatch(r'[A-Z][A-Z0-9_]{1,15}',key): raise DomainError('Project key: 2–16 uppercase letters, digits or underscore')
        values={'key':key,'name':clean_text(data.get('name',''),160,'name',True),'description':clean_text(data.get('description',''),40000,'description'),'status':choice(data.get('status','active'),PROJECT_STATUSES,'status'),'category':clean_text(data.get('category','Product'),40,'category'),'owner':clean_text(data.get('owner','Evan'),80,'owner'),'source':clean_text(data.get('source',''),20000,'source'),'source_key':data.get('source_key')}
        with self.tx() as s:
            if values['source_key']:
                existing=s.scalar(select(Project).where(Project.source_key==values['source_key']))
                if existing:return serialize(existing)
            p=Project(**values);s.add(p);s.flush();self.log(s,p.id,'project_created',p.name,actor);return serialize(p)
    def list_projects(self):
        with self.Session() as s:
            result=[]
            for p in s.scalars(select(Project).order_by(Project.name)):
                d=serialize(p);rows=s.execute(select(Issue.status,func.count()).where(Issue.project_id==p.id).group_by(Issue.status)).all();d['counts']=dict(rows);d['total']=sum(d['counts'].values());d['done']=d['counts'].get('done',0)
                d['next_action']=s.scalar(select(Issue.title).where(Issue.project_id==p.id,Issue.status!='done').order_by(Issue.updated_at.desc()).limit(1))
                result.append(d)
            return result
    def get_project(self, ref):
        with self.Session() as s:
            project = self.project(s, ref)
            issues = Issue.project_id == project.id
            counts = dict(s.execute(select(Issue.status, func.count()).where(issues).group_by(Issue.status)).all())
            epics = s.scalars(select(Issue).where(issues, Issue.type == 'epic').order_by(Issue.number)).all()
            next_issue = s.scalar(select(Issue).where(issues, Issue.status != 'done').order_by(
                Issue.updated_at.desc(), Issue.id,
            ).limit(1))
            fields = ('id', 'key', 'title', 'status', 'type', 'priority', 'parent_id')
            def summary(issue):
                record = self.issue_dict(s, issue)
                return {field: record[field] for field in fields}
            return {**serialize(project), 'counts': counts, 'total': sum(counts.values()),
                    'epics': [summary(epic) for epic in epics],
                    'next_issue': summary(next_issue) if next_issue else None}
    def update_project(self,ref,data,version,actor='Evan'):
        if not isinstance(version,int): raise DomainError('expected_version required')
        if set(data)-{'name','description','status','category','owner','source'}:raise DomainError('Unknown project fields')
        with self.tx() as s:
            p=self.project(s,ref,True)
            if p.version!=version:raise DomainError('Project changed; reload before saving',409)
            limits={'name':160,'description':40000,'source':20000,'category':40,'owner':80}
            for k,v in data.items():setattr(p,k,choice(v,PROJECT_STATUSES,k) if k=='status' else clean_text(v,limits[k],k,k=='name'))
            p.version+=1;p.updated_at=now();self.log(s,p.id,'project_updated',json.dumps(data),actor);s.flush();return serialize(p)
    def _new_issue(self,s,project,data,actor):
        allowed={'title','description','status','priority','type','owner','due_date','labels','parent_id','source','source_key','resolution'}
        if set(data)-allowed:raise DomainError('Unknown issue fields')
        if data.get('source_key'):
            clean_text(data['source_key'],180,'source_key',True)
            existing=s.scalar(select(Issue).where(Issue.source_key==data['source_key']))
            if existing:
                if existing.project_id!=project.id:raise DomainError('Import key belongs to another project',409)
                return existing
        parent=data.get('parent_id')
        if parent:
            parent=self.issue(s,parent)
            if parent.project_id!=project.id:raise DomainError('Subtasks must belong to their parent project')
            parent=parent.id
        due=data.get('due_date') or None
        if due:
            try:dt.date.fromisoformat(due)
            except (TypeError,ValueError):raise DomainError('Invalid due date')
        labels=data.get('labels',[])
        if not isinstance(labels,list) or len(labels)>20 or any(not isinstance(x,str) or len(x)>40 for x in labels):raise DomainError('Invalid labels')
        i=Issue(project_id=project.id,number=project.next_number,title=clean_text(data.get('title',''),240,'title',True),description=clean_text(data.get('description',''),80000,'description'),status=choice(data.get('status','backlog'),STATUSES,'status'),priority=choice(data.get('priority','medium'),PRIORITIES,'priority'),type=choice(data.get('type','task'),TYPES,'type'),owner=clean_text(data.get('owner','Evan'),80,'owner'),due_date=due,labels=json.dumps(labels),parent_id=parent,source=clean_text(data.get('source',''),20000,'source'),source_key=data.get('source_key'),resolution=clean_text(data.get('resolution',''),40000,'resolution'))
        project.next_number+=1;s.add(i);s.flush();self.log(s,i.id,'issue_created',i.title,actor);return i
    def create_issue(self,project,data,actor='Evan'):
        with self.tx() as s:
            p=self.project(s,project,True);i=self._new_issue(s,p,data,actor);return self.issue_dict(s,i)
    def add_subtasks(self,ref,tasks,actor='Evan'):
        if not isinstance(tasks,list) or not 1<=len(tasks)<=20:raise DomainError('Provide 1–20 subtasks')
        with self.tx() as s:
            parent=self.issue(s,ref);p=self.project(s,parent.project_id,True)
            result=[]
            for task in tasks:
                if not isinstance(task,dict) or set(task)-{'title','description','source_key'}:raise DomainError('Invalid subtask')
                i=self._new_issue(s,p,{**task,'parent_id':parent.id},actor);result.append(self.issue_dict(s,i))
            return result
    def list_issues(self,project=None,status=None,q='',limit=200,offset=0):
        if not 1<=limit<=500 or offset<0:raise DomainError('Invalid pagination')
        if len(q)>200:raise DomainError('Search is too long')
        with self.Session() as s:
            st=select(Issue).join(Project)
            if project:st=st.where(Issue.project_id==self.project(s,project).id)
            if status:st=st.where(Issue.status==choice(status,STATUSES,'status'))
            if q:
                pattern='%'+q.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
                criteria=or_(Issue.title.like(pattern,escape='\\'),Issue.description.like(pattern,escape='\\'),Project.name.like(pattern,escape='\\'))
                match=re.fullmatch(r'([A-Za-z][A-Za-z0-9_]{1,15})-(\d+)',q)
                if match:criteria=or_(criteria,and_(Project.key==match.group(1).upper(),Issue.number==int(match.group(2))))
                st=st.where(criteria)
            total=s.scalar(select(func.count()).select_from(st.subquery()))
            rows=s.scalars(st.order_by(Issue.updated_at.desc(),Issue.id).offset(offset).limit(limit))
            return {'items':[self.issue_dict(s,i) for i in rows],'total':total,'limit':limit,'offset':offset}
    def get_issue(self,ref):
        with self.Session() as s:
            i=self.issue(s,ref);d=self.issue_dict(s,i);d['comments']=[serialize(c) for c in s.scalars(select(Comment).where(Comment.issue_id==i.id).order_by(Comment.created_at,Comment.id))];d['subtasks']=[self.issue_dict(s,c) for c in s.scalars(select(Issue).where(Issue.parent_id==i.id).order_by(Issue.number))];d['links']=[{**serialize(link),'other':self.issue_dict(s,s.get(Issue,link.issue_b if link.issue_a==i.id else link.issue_a))} for link in s.scalars(select(Link).where(or_(Link.issue_a==i.id,Link.issue_b==i.id)))];d['activity']=[serialize(a) for a in s.scalars(select(Activity).where(Activity.entity_id==i.id).order_by(Activity.id.desc()).limit(100))];return d
    def update_issue(self,ref,data,version,actor='Evan'):
        allowed={'title','description','status','priority','type','owner','due_date','labels','resolution','source'}
        if set(data)-allowed:raise DomainError('Unknown issue fields')
        if not isinstance(version,int):raise DomainError('expected_version required')
        with self.tx() as s:
            i=self.issue(s,ref,True)
            if i.version!=version:raise DomainError('Issue changed; reload before saving',409)
            for k,v in data.items():
                if k in ('status','priority','type'):v=choice(v,{'status':STATUSES,'priority':PRIORITIES,'type':TYPES}[k],k)
                elif k=='labels':
                    if not isinstance(v,list) or len(v)>20 or any(not isinstance(x,str) or len(x)>40 for x in v):raise DomainError('Invalid labels')
                    v=json.dumps(v)
                elif k=='due_date':
                    if v:
                        try:dt.date.fromisoformat(v)
                        except (TypeError,ValueError):raise DomainError('Invalid due date')
                    else:v=None
                else:
                    limits={'title':240,'description':80000,'resolution':40000,'source':20000,'owner':80}
                    v=clean_text(v,limits[k],k,k=='title')
                setattr(i,k,v)
            i.version+=1;i.updated_at=now();self.log(s,i.id,'issue_updated',json.dumps(data),actor);s.flush();return self.issue_dict(s,i)
    def add_comment(self,ref,body,actor='Evan'):
        body=clean_text(body,20000,'comment',True)
        with self.tx() as s:
            i=self.issue(s,ref);c=Comment(issue_id=i.id,body=body,author=actor);s.add(c);self.log(s,i.id,'comment_added','',actor);s.flush();return serialize(c)
    def export(self):
        with self.Session() as s:return {table.__tablename__:[serialize(r) for r in s.scalars(select(table))] for table in (Project,Issue,Comment,Activity,Link)}

    def link_issues(self,source,target,kind='related',actor='Evan'):
        choice(kind,('related','blocks'),'link kind')
        with self.tx() as s:
            if kind == 'blocks':
                s.execute(update(DependencyGraph).where(DependencyGraph.id == 1)
                          .values(generation=1 - DependencyGraph.generation))
            a=self.issue(s,source);b=self.issue(s,target)
            if a.id==b.id:raise DomainError('An issue cannot link to itself')
            aid,bid=a.id,b.id
            if kind=='related':aid,bid=sorted((aid,bid))
            if kind=='blocks':
                edges={}
                for edge in s.scalars(select(Link).where(Link.kind=='blocks')):edges.setdefault(edge.issue_a,[]).append(edge.issue_b)
                pending=[bid];seen=set()
                while pending:
                    node=pending.pop()
                    if node==aid:raise DomainError('Blocking links cannot form a cycle')
                    if node not in seen:seen.add(node);pending.extend(edges.get(node,[]))
            existing=s.scalar(select(Link).where(Link.issue_a==aid,Link.issue_b==bid,Link.kind==kind))
            if existing:return serialize(existing)
            link=Link(issue_a=aid,issue_b=bid,kind=kind);s.add(link);s.flush();self.log(s,a.id,'issue_linked',f'{kind}: {target}',actor);return serialize(link)
